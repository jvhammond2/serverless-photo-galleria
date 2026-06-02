import json
import os
import boto3
import stripe

TIERS = {
    "small":  {"bucket_env": "THUMBS_BUCKET",    "key_field": "thumbnailKey"},
    "medium": {"bucket_env": "PREVIEWS_BUCKET",  "key_field": "previewKey"},
    "large":  {"bucket_env": "ORIGINALS_BUCKET", "key_field": "originalKey"},
}

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}

def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    ssm = boto3.client("ssm")
    stripe.api_key = ssm.get_parameter(
        Name=os.environ["STRIPE_SECRET_KEY_PARAM"], WithDecryption=True
    )["Parameter"]["Value"]

    params     = event.get("queryStringParameters") or {}
    session_id = params.get("session_id", "").strip()
    if not session_id:
        return _err(400, "session_id is required")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.InvalidRequestError:
        return _err(404, "Session not found")

    if session.payment_status != "paid":
        return _err(402, "Payment not completed")

    meta  = session.metadata or {}
    multi = meta.get("multi", "0") == "1"

    if multi:
        return _handle_multi(session, meta)
    else:
        return _handle_single(session, meta)


def _handle_single(session, meta):
    photo_id = meta.get("photoId", "")
    tier     = meta.get("tier", "")
    if not photo_id or tier not in TIERS:
        return _err(400, "Invalid session metadata")

    table    = boto3.resource("dynamodb").Table(os.environ["METADATA_TABLE"])
    resp     = table.get_item(Key={"photoId": photo_id})
    item     = resp.get("Item")
    if not item:
        return _err(404, "Photo not found")

    dl = _make_download(item, tier)
    if not dl:
        return _err(404, "File not found for this tier")

    _record_orders(session, [{"photoId": photo_id, "tier": tier,
                               "fileName": item.get("fileName", photo_id)}])

    return {
        "statusCode": 200,
        "headers": CORS,
        "body": json.dumps({"downloads": [dl]}),
    }


def _handle_multi(session, meta):
    photo_pairs_str = meta.get("photoIds", "")
    if not photo_pairs_str:
        return _err(400, "No photos in session metadata")

    table     = boto3.resource("dynamodb").Table(os.environ["METADATA_TABLE"])
    downloads = []
    orders    = []

    for pair in photo_pairs_str.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        photo_id, tier = pair.rsplit(":", 1)
        photo_id = photo_id.strip(); tier = tier.strip()
        if tier not in TIERS:
            continue
        resp = table.get_item(Key={"photoId": photo_id})
        item = resp.get("Item")
        if not item:
            continue
        dl = _make_download(item, tier)
        if dl:
            downloads.append(dl)
            orders.append({"photoId": photo_id, "tier": tier,
                            "fileName": item.get("fileName", photo_id)})

    if not downloads:
        return _err(404, "No downloadable files found")

    _record_orders(session, orders)

    return {
        "statusCode": 200,
        "headers": CORS,
        "body": json.dumps({"downloads": downloads}),
    }


def _make_download(item, tier):
    """Generate a 24-hour presigned S3 download URL for one photo+tier."""
    tier_cfg  = TIERS[tier]
    s3_key    = item.get(tier_cfg["key_field"], "")
    bucket    = os.environ[tier_cfg["bucket_env"]]
    file_name = item.get("fileName", item.get("photoId", "photo") + ".jpg")

    if not s3_key:
        return None

    s3  = boto3.client("s3")
    url = s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket":                     bucket,
            "Key":                        s3_key,
            "ResponseContentDisposition": f'attachment; filename="{file_name}"',
        },
        ExpiresIn=86400,
    )
    return {"photoId": item.get("photoId", ""), "fileName": file_name,
            "tier": tier, "downloadUrl": url}


def _record_orders(session, items):
    """Idempotent — safe to call on every get-download request."""
    try:
        table = boto3.resource("dynamodb").Table(os.environ["ORDERS_TABLE"])
        for idx, it in enumerate(items):
            # For multi-item sessions, suffix the sessionId to keep PK unique
            pk = session.id if len(items) == 1 else f"{session.id}#{idx}"
            table.put_item(
                Item={
                    "sessionId":     pk,
                    "photoId":       it["photoId"],
                    "tier":          it["tier"],
                    "fileName":      it["fileName"],
                    "customerEmail": session.customer_email or "",
                    "amountTotal":   session.amount_total,
                    "status":        "paid",
                    "createdAt":     str(session.created),
                },
                ConditionExpression="attribute_not_exists(sessionId)",
            )
    except Exception:
        pass


def _err(code, msg):
    return {
        "statusCode": code,
        "headers": CORS,
        "body": json.dumps({"error": msg}),
    }
