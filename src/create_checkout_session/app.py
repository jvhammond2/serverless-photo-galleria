import json
import os
import boto3
import stripe

TIERS = {
    "small": {
        "label":       "Small (Web)",
        "description": "72 DPI · up to 1,000 px · perfect for screen use",
        "unit_amount": 999,
        "bucket_env":  "THUMBS_BUCKET",
        "key_field":   "thumbnailKey",
    },
    "medium": {
        "label":       "Medium (Print)",
        "description": "150 DPI · up to 3,000 px · great for home printing",
        "unit_amount": 2999,
        "bucket_env":  "PREVIEWS_BUCKET",
        "key_field":   "previewKey",
    },
    "large": {
        "label":       "Large (Pro)",
        "description": "Full original resolution · maximum quality",
        "unit_amount": 4999,
        "bucket_env":  "ORIGINALS_BUCKET",
        "key_field":   "originalKey",
    },
}

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}

def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    ssm = boto3.client("ssm")
    stripe.api_key = ssm.get_parameter(
        Name=os.environ["STRIPE_SECRET_KEY_PARAM"], WithDecryption=True
    )["Parameter"]["Value"]

    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        return _err(400, "Invalid JSON body")

    claims         = (event.get("requestContext", {})
                          .get("authorizer", {})
                          .get("claims", {}))
    customer_email = claims.get("email", "")
    customer_sub   = claims.get("sub", "")
    customer_site  = os.environ["CUSTOMER_SITE_URL"].rstrip("/")
    table          = boto3.resource("dynamodb").Table(os.environ["METADATA_TABLE"])

    # ── Multi-item (cart) checkout ────────────────────────────────────────────
    items_input = body.get("items")  # [{photoId, tier}, ...]
    if items_input and isinstance(items_input, list):
        line_items  = []
        photo_pairs = []   # "photoId:tier" for metadata

        for entry in items_input:
            pid  = entry.get("photoId", "").strip()
            tier = entry.get("tier", "medium").strip().lower()
            if not pid or tier not in TIERS:
                continue
            resp     = table.get_item(Key={"photoId": pid})
            ddb_item = resp.get("Item")
            if not ddb_item:
                continue
            tier_cfg  = TIERS[tier]
            file_name = ddb_item.get("fileName", pid)
            photo_name = file_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()
            line_items.append({
                "price_data": {
                    "currency":     "usd",
                    "unit_amount":  tier_cfg["unit_amount"],
                    "product_data": {
                        "name":        f"{photo_name} — {tier_cfg['label']}",
                        "description": tier_cfg["description"],
                    },
                },
                "quantity": 1,
            })
            photo_pairs.append(f"{pid}:{tier}")

        if not line_items:
            return _err(400, "No valid items found")

        # Stripe metadata values max 500 chars — fine for typical cart sizes
        meta_photos = ",".join(photo_pairs)
        if len(meta_photos) > 490:
            meta_photos = meta_photos[:490]

        success_url = f"{customer_site}?success=1&session_id={{CHECKOUT_SESSION_ID}}"
        cancel_url  = f"{customer_site}?cancelled=1"

        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=line_items,
            mode="payment",
            success_url=success_url,
            cancel_url=cancel_url,
            customer_email=customer_email or None,
            metadata={"photoIds": meta_photos, "multi": "1", "userId": customer_sub},
        )
        return {
            "statusCode": 200,
            "headers": CORS,
            "body": json.dumps({"sessionUrl": session.url, "sessionId": session.id}),
        }

    # ── Single-item checkout ──────────────────────────────────────────────────
    photo_id = body.get("photoId", "").strip()
    tier     = body.get("tier", "").strip().lower()

    if not photo_id:
        return _err(400, "photoId is required")
    if tier not in TIERS:
        return _err(400, f"tier must be one of: {', '.join(TIERS)}")

    resp     = table.get_item(Key={"photoId": photo_id})
    item     = resp.get("Item")
    if not item:
        return _err(404, "Photo not found")

    tier_cfg   = TIERS[tier]
    file_name  = item.get("fileName", photo_id)
    photo_name = file_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title()

    success_url = (f"{customer_site}?success=1&session_id={{CHECKOUT_SESSION_ID}}"
                   f"&photo={photo_id}&tier={tier}")
    cancel_url  = f"{customer_site}?cancelled=1"

    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency":     "usd",
                "unit_amount":  tier_cfg["unit_amount"],
                "product_data": {
                    "name":        f"{photo_name} — {tier_cfg['label']}",
                    "description": tier_cfg["description"],
                },
            },
            "quantity": 1,
        }],
        mode="payment",
        success_url=success_url,
        cancel_url=cancel_url,
        customer_email=customer_email or None,
        metadata={
            "photoId":    photo_id,
            "tier":       tier,
            "fileName":   file_name,
            "key_field":  tier_cfg["key_field"],
            "bucket_env": tier_cfg["bucket_env"],
            "multi":      "0",
            "userId":     customer_sub,
        },
    )
    return {
        "statusCode": 200,
        "headers": CORS,
        "body": json.dumps({"sessionUrl": session.url, "sessionId": session.id}),
    }


def _err(code, msg):
    return {
        "statusCode": code,
        "headers": CORS,
        "body": json.dumps({"error": msg}),
    }
