"""
CollectorFunction — src/collector/app.py
------------------------------------------
GET /my-collection   (requires Authorization header — Cognito JWT)

Returns the authenticated buyer's purchased photos with:
  - Thumbnail URL (CloudFront — free, no presigning needed)
  - Re-download presigned URL (short-lived, 15 min — buyer triggers on demand)
  - Purchase metadata: tier, purchasedAt, title, photographer

Design (cost-conscious):
  - CollectionsTable uses a composite key (buyerId PK + photoId SK) so a
    single Query with buyerId = sub returns the entire collection in one
    DynamoDB round-trip.
  - Thumbnails use CloudFront (no S3 cost per request).
  - Re-download presigned URLs are generated per-request, not stored —
    no TTL management or scheduled cleanup needed.
  - Pagination with cursor keeps payloads small for large collections.

AWS Cert Note (SAA-C03 / DVA-C02):
  DynamoDB composite key: Query on PK alone returns all items for that buyer
  in O(result-set) time.  No GSI needed because we only ever query by buyerId.
  Presigned URLs delegate s3:GetObject permission to the bearer without
  exposing AWS credentials — the buyer's browser downloads directly from S3.
"""

import base64
import json
import os

import boto3
from boto3.dynamodb.conditions import Key

COLLECTIONS_TABLE = os.environ["COLLECTIONS_TABLE"]
THUMBS_URL        = os.environ.get("THUMBS_DISTRIBUTION_URL", "").rstrip("/")
PREVIEWS_BUCKET   = os.environ["PREVIEWS_BUCKET"]
ORIGINALS_BUCKET  = os.environ["ORIGINALS_BUCKET"]
THUMBS_BUCKET     = os.environ["THUMBS_BUCKET"]

REDOWNLOAD_TTL = 900   # 15 minutes
DEFAULT_LIMIT  = 24
MAX_LIMIT      = 100

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}

TIER_BUCKET = {
    "small":  THUMBS_BUCKET,
    "medium": PREVIEWS_BUCKET,
    "large":  ORIGINALS_BUCKET,
}
TIER_KEY = {
    "small":  "thumbnailKey",
    "medium": "previewKey",
    "large":  "originalKey",
}

ddb = boto3.resource("dynamodb")
s3  = boto3.client("s3")


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return _ok({})

    # Extract Cognito sub from JWT
    buyer_id = _extract_sub(event)
    if not buyer_id:
        return _err(401, "Authorization header required")

    params = event.get("queryStringParameters") or {}
    try:
        limit = min(int(params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT
    cursor_str = (params.get("cursor") or "").strip()

    table = ddb.Table(COLLECTIONS_TABLE)
    query_kwargs = {
        "KeyConditionExpression": Key("buyerId").eq(buyer_id),
        "ScanIndexForward": False,   # newest first
        "Limit": limit,
    }
    if cursor_str:
        start_key = _decode_cursor(cursor_str)
        if start_key:
            query_kwargs["ExclusiveStartKey"] = start_key

    resp     = table.query(**query_kwargs)
    items    = resp.get("Items", [])
    last_key = resp.get("LastEvaluatedKey")

    photos = []
    for item in items:
        photo_id  = item.get("photoId", "")
        tier      = item.get("tier", "medium")
        s3_key    = item.get(TIER_KEY.get(tier, "previewKey"), "")
        thumb_key = item.get("thumbnailKey", "")

        photos.append({
            "photoId":          photo_id,
            "title":            item.get("title", photo_id),
            "tier":             tier,
            "purchasedAt":      item.get("purchasedAt", ""),
            "photographerId":   item.get("photographerId", ""),
            "photographerName": item.get("photographerName", ""),
            "thumbnailUrl":     f"{THUMBS_URL}/{thumb_key}" if thumb_key and THUMBS_URL else "",
            "redownloadUrl":    _presign(tier, s3_key, item.get("title", photo_id)) if s3_key else None,
        })

    return _ok({
        "photos":     photos,
        "count":      len(photos),
        "nextCursor": _encode_cursor(last_key) if last_key else None,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_sub(event: dict) -> str | None:
    """Decode Cognito sub from the Authorization JWT without verifying signature."""
    auth = (event.get("headers") or {}).get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not token:
        return None
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.b64decode(payload_b64))
        return claims.get("sub") or claims.get("email")
    except Exception:
        return None


def _presign(tier: str, s3_key: str, title: str) -> str | None:
    bucket = TIER_BUCKET.get(tier)
    if not bucket or not s3_key:
        return None
    fname = s3_key.rsplit("/", 1)[-1]
    try:
        return s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket":                     bucket,
                "Key":                        s3_key,
                "ResponseContentDisposition": f'attachment; filename="{fname}"',
            },
            ExpiresIn=REDOWNLOAD_TTL,
        )
    except Exception:
        return None


def _encode_cursor(last_key: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(last_key).encode()).decode()


def _decode_cursor(cursor: str) -> dict | None:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception:
        return None


def _ok(body: dict):
    return {
        "statusCode": 200,
        "headers":    {**CORS, "Content-Type": "application/json"},
        "body":       json.dumps(body),
    }


def _err(code: int, msg: str):
    return {
        "statusCode": code,
        "headers":    {**CORS, "Content-Type": "application/json"},
        "body":       json.dumps({"error": msg}),
    }
