"""
StripeWebhookFunction — src/stripe_webhook/app.py
---------------------------------------------------
POST /stripe-webhook  (no auth — verified by Stripe signature)

Handles checkout.session.completed:
  1. Verify Stripe webhook signature (rejects forged requests).
  2. Record each purchased photo in OrdersTable (idempotent via ConditionExpression).
  3. Generate 24-hour presigned S3 download URL(s) and email them to the buyer via SES.

The presigned URL email is a backup delivery channel — the primary path is the
in-browser success overlay which calls GET /get-download directly.  The email
ensures the buyer can always retrieve files even if they close the tab.

AWS Cert Note (SAA-C03 / DVA-C02):
  Stripe calls this endpoint asynchronously after payment confirmation.
  Always return HTTP 200 to Stripe even if internal processing fails —
  Stripe retries non-2xx responses for 3 days, which could spam the buyer
  with duplicate emails.  Log failures internally instead of surfacing them.
"""

import json
import os
import boto3
import stripe

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Stripe-Signature",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}

# Bucket names resolved from env vars at cold start
TIER_CONFIG = {
    "small":  {"bucket_env": "THUMBS_BUCKET",   "key_field": "thumbnailKey"},
    "medium": {"bucket_env": "PREVIEWS_BUCKET",  "key_field": "previewKey"},
    "large":  {"bucket_env": "ORIGINALS_BUCKET", "key_field": "originalKey"},
}

DOWNLOAD_TTL = 86400  # 24 hours


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    ssm = boto3.client("ssm")
    stripe.api_key = ssm.get_parameter(
        Name=os.environ["STRIPE_SECRET_KEY_PARAM"], WithDecryption=True
    )["Parameter"]["Value"]
    webhook_secret = ssm.get_parameter(
        Name=os.environ["STRIPE_WEBHOOK_SECRET_PARAM"], WithDecryption=True
    )["Parameter"]["Value"]

    sig_header = (event.get("headers") or {}).get("Stripe-Signature", "")
    payload    = event.get("body", "")

    try:
        stripe_event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        return {"statusCode": 400, "headers": CORS, "body": json.dumps({"error": "Invalid signature"})}
    except Exception as e:
        return {"statusCode": 400, "headers": CORS, "body": json.dumps({"error": str(e)})}

    if stripe_event["type"] == "checkout.session.completed":
        _handle_completed(stripe_event["data"]["object"])

    # Always return 200 to Stripe regardless of internal errors
    return {"statusCode": 200, "headers": CORS, "body": json.dumps({"received": True})}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _handle_completed(session):
    meta           = session.get("metadata") or {}
    customer_email = session.get("customer_email", "")
    amount_total   = session.get("amount_total", 0)
    session_id     = session.get("id", "")
    user_id        = meta.get("userId", "")
    payment_intent = session.get("payment_intent", "")
    created_at     = str(session.get("created", ""))
    is_multi       = meta.get("multi", "0") == "1"

    orders_table       = boto3.resource("dynamodb").Table(os.environ["ORDERS_TABLE"])
    collections_table  = boto3.resource("dynamodb").Table(os.environ["COLLECTIONS_TABLE"])

    if is_multi:
        pairs = _parse_multi_meta(meta.get("photoIds", ""))
    else:
        photo_id = meta.get("photoId", "").strip()
        tier     = meta.get("tier", "").strip()
        if not photo_id or tier not in TIER_CONFIG:
            print(f"[webhook] Invalid single-item metadata: {meta}")
            return
        pairs = [(photo_id, tier)]

    if not pairs:
        print(f"[webhook] No valid photo/tier pairs in session {session_id}")
        return

    # ── 1. Look up DynamoDB metadata for all purchased photos ────────────────
    metadata_table = boto3.resource("dynamodb").Table(os.environ["METADATA_TABLE"])
    downloads = []
    for idx, (photo_id, tier) in enumerate(pairs):
        resp = metadata_table.get_item(Key={"photoId": photo_id})
        item = resp.get("Item")
        if not item:
            print(f"[webhook] Photo {photo_id} not found in metadata table — skipping")
            continue

        # ── 2. Generate presigned download URL ────────────────────────────
        dl = _make_presigned(item, tier)
        if dl:
            downloads.append(dl)

        # ── 3. Record order (idempotent) ──────────────────────────────────
        pk = session_id if len(pairs) == 1 else f"{session_id}#{idx}"
        try:
            orders_table.put_item(
                Item={
                    "sessionId":       pk,
                    "photoId":         photo_id,
                    "tier":            tier,
                    "fileName":        item.get("fileName", photo_id),
                    "customerEmail":   customer_email,
                    "userId":          user_id,
                    "amountTotal":     amount_total,
                    "paymentIntentId": payment_intent,
                    "status":          "paid",
                    "createdAt":       created_at,
                    "source":          "webhook",
                },
                ConditionExpression="attribute_not_exists(sessionId)",
            )
        except Exception:
            pass  # duplicate — already recorded by get_download

        # ── 3b. Add to buyer's collection (idempotent, cost: ~1 WCU) ─────
        # AWS Cert Note (DVA-C02): put_item with attribute_not_exists is
        # idempotent — safe to replay if Stripe retries this webhook.
        if user_id:
            try:
                collections_table.put_item(
                    Item={
                        "buyerId":          user_id,
                        "photoId":          photo_id,
                        "tier":             tier,
                        "purchasedAt":      created_at,
                        "sessionId":        session_id,
                        "customerEmail":    customer_email,
                        "photographerId":   item.get("photographerId", ""),
                        "photographerName": item.get("photographerName", ""),
                        "title":            item.get("title", item.get("fileName", photo_id)),
                        "thumbnailKey":     item.get("thumbnailKey", ""),
                        "previewKey":       item.get("previewKey", ""),
                        "originalKey":      item.get("originalKey", ""),
                    },
                    ConditionExpression="attribute_not_exists(buyerId) AND attribute_not_exists(photoId)",
                )
                print(f"[webhook] Collection item recorded: buyerId={user_id} photoId={photo_id}")
            except Exception:
                pass  # duplicate purchase — already in collection

    # ── 4. Send email with download links ────────────────────────────────────
    if downloads and customer_email:
        try:
            _send_download_email(customer_email, downloads, amount_total)
        except Exception as e:
            # Non-fatal — buyer can always use the in-browser success overlay
            print(f"[webhook] SES email failed (non-fatal): {e}")
    else:
        print(f"[webhook] No email sent: downloads={len(downloads)}, email='{customer_email}'")


def _parse_multi_meta(raw: str) -> list[tuple[str, str]]:
    pairs = []
    for pair in raw.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        photo_id, tier = pair.rsplit(":", 1)
        photo_id = photo_id.strip()
        tier     = tier.strip()
        if photo_id and tier in TIER_CONFIG:
            pairs.append((photo_id, tier))
    return pairs


def _make_presigned(item: dict, tier: str) -> dict | None:
    cfg     = TIER_CONFIG[tier]
    s3_key  = item.get(cfg["key_field"], "")
    bucket  = os.environ[cfg["bucket_env"]]
    fname   = item.get("fileName", item.get("photoId", "photo") + ".jpg")
    title   = item.get("title") or fname

    if not s3_key:
        return None

    s3  = boto3.client("s3")
    url = s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket":                     bucket,
            "Key":                        s3_key,
            "ResponseContentDisposition": f'attachment; filename="{fname}"',
        },
        ExpiresIn=DOWNLOAD_TTL,
    )
    return {"title": title, "fileName": fname, "tier": tier, "downloadUrl": url}


def _send_download_email(to_email: str, downloads: list, amount_total: int):
    """Send an SES email with download button(s) for each purchased file."""
    ses        = boto3.client("ses")
    from_email = os.environ["SES_FROM_EMAIL"]
    count      = len(downloads)
    amount_usd = f"${amount_total / 100:.2f}"

    TIER_LABEL = {"small": "Small (Web)", "medium": "Medium (Print)", "large": "Large (Pro)"}

    # Build HTML rows for each download
    rows_html = ""
    rows_text = ""
    for d in downloads:
        label = TIER_LABEL.get(d["tier"], d["tier"].title())
        rows_html += f"""
        <tr>
          <td style="padding:12px 0;border-bottom:1px solid #eee;">
            <strong style="color:#111;">{_escape_html(d['title'])}</strong><br>
            <span style="font-size:12px;color:#777;">{label}</span>
          </td>
          <td style="padding:12px 0;border-bottom:1px solid #eee;text-align:right;">
            <a href="{d['downloadUrl']}"
               style="display:inline-block;padding:8px 18px;background:#2d6a4f;color:#fff;
                      text-decoration:none;border-radius:6px;font-size:13px;font-weight:500;">
              Download
            </a>
          </td>
        </tr>"""
        rows_text += f"  {d['title']} ({label})\n  {d['downloadUrl']}\n\n"

    file_word  = "file" if count == 1 else "files"
    subject    = f"Your Galleria {'download is' if count == 1 else 'downloads are'} ready"

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:#f5f5f0;font-family:'Helvetica Neue',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#f5f5f0;padding:40px 0;">
    <tr><td align="center">
      <table width="520" cellpadding="0" cellspacing="0"
             style="background:#fff;border-radius:12px;overflow:hidden;
                    box-shadow:0 4px 24px rgba(0,0,0,.08);">

        <!-- Header -->
        <tr>
          <td style="background:#111;padding:32px 40px;text-align:center;">
            <span style="font-family:'Georgia',serif;font-size:22px;color:#fff;
                         letter-spacing:.12em;">GALLERIA</span>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:40px 40px 32px;">
            <h1 style="margin:0 0 8px;font-size:22px;font-weight:600;color:#111;">
              Your {file_word} {'is' if count == 1 else 'are'} ready
            </h1>
            <p style="margin:0 0 28px;color:#555;font-size:14px;line-height:1.6;">
              Thank you for your purchase ({amount_usd}).
              Your download link{'s' if count > 1 else ''} will expire in 24 hours.
            </p>

            <!-- Download table -->
            <table width="100%" cellpadding="0" cellspacing="0">
              {rows_html}
            </table>

            <p style="margin:28px 0 0;font-size:12px;color:#999;line-height:1.6;">
              Didn't request this? You can safely ignore this email.<br>
              Having trouble? Reply to this email for support.
            </p>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:20px 40px;background:#f9f9f7;border-top:1px solid #eee;
                     text-align:center;">
            <span style="font-size:11px;color:#aaa;">
              © Galleria — Fine Art Photography Prints
            </span>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

    text_body = (
        f"Your Galleria {file_word} {'is' if count == 1 else 'are'} ready\n"
        f"{'=' * 50}\n\n"
        f"Thank you for your purchase ({amount_usd}).\n"
        f"Download links expire in 24 hours.\n\n"
        f"{rows_text}"
        f"Having trouble? Reply to this email for support.\n\n"
        f"— Galleria Fine Art Photography Prints"
    )

    ses.send_email(
        Source=from_email,
        Destination={"ToAddresses": [to_email]},
        Message={
            "Subject": {"Data": subject, "Charset": "UTF-8"},
            "Body": {
                "Text": {"Data": text_body,  "Charset": "UTF-8"},
                "Html": {"Data": html_body, "Charset": "UTF-8"},
            },
        },
    )
    print(f"[webhook] Download email sent to {to_email} for {count} file(s)")


def _escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
