"""
FeedFunction — src/feed/app.py
---------------------------------
GET /feed

Returns the most recent photos from photographers the authenticated customer
follows, newest-first.  Requires CustomerCognito authorizer.

Query parameters:
  limit   Page size (default 24, max 100)
  cursor  Base64-encoded DynamoDB ExclusiveStartKey for next page

Algorithm (two-step read):
  1. Query FollowsTable by followerId → get the list of followeeIds.
  2. Scan PhotoMetadataTable with FilterExpression: photographerId IN (list).
  3. Sort by uploadedAt descending, paginate, return.

Why a Scan rather than a GSI per photographer?
  For MVP gallery sizes a filtered Scan is simple and cost-negligible at
  zero traffic.  If the library grows large, add a GSI
  (photographerId HASH + uploadedAt RANGE) and convert the Scan to a
  BatchQuery — no schema change needed; just add the index.

AWS Cert Note (SAA-C03 / DVA-C02):
  The two-table read here is a common NoSQL "fan-out read" pattern.  At
  scale the alternative is a "pre-computed feed" (write a record to a
  FeedTable for every follower on every upload — expensive to write, O(1)
  to read).  For low-traffic MVPs, the lazy read approach is cheaper.
"""

import base64
import boto3
import json
import os
from decimal import Decimal

from aws_xray_sdk.core import patch_all, xray_recorder
patch_all()

dynamodb = boto3.resource("dynamodb")
s3       = boto3.client("s3")

FOLLOWS_TABLE           = os.environ["FOLLOWS_TABLE"]
METADATA_TABLE          = os.environ["METADATA_TABLE"]
THUMBS_DISTRIBUTION_URL = os.environ["THUMBS_DISTRIBUTION_URL"].rstrip("/")
PREVIEWS_BUCKET         = os.environ["PREVIEWS_BUCKET"]

DEFAULT_PAGE_SIZE = 24
MAX_PAGE_SIZE     = 100
PREVIEW_TTL       = 300   # 5 minutes

HEADERS = {
    "Content-Type":                "application/json",
    "Access-Control-Allow-Origin": "*",
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _caller_id(event: dict) -> str:
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email") or ""


def _error(status: int, msg: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


def _encode_cursor(last_key: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(last_key).encode()).decode()


def _decode_cursor(cursor: str):
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception:
        return None


def _thumb_url(key: str) -> str:
    return f"{THUMBS_DISTRIBUTION_URL}/{key}"


def _preview_url(key: str) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": PREVIEWS_BUCKET, "Key": key},
        ExpiresIn=PREVIEW_TTL,
    )


def handler(event, context):
    caller = _caller_id(event)
    if not caller:
        return _error(401, "Unauthorized")

    params     = event.get("queryStringParameters") or {}
    cursor_str = (params.get("cursor") or "").strip()
    try:
        limit = min(int(params.get("limit", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    except (ValueError, TypeError):
        limit = DEFAULT_PAGE_SIZE

    # ── Step 1: get the list of followed photographer IDs ────────────────────
    follows_table = dynamodb.Table(FOLLOWS_TABLE)
    resp          = follows_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("followerId").eq(caller),
        ProjectionExpression="followeeId",
    )
    followee_ids = [item["followeeId"] for item in resp.get("Items", [])]

    _annotate(userId=caller, followingCount=str(len(followee_ids)))

    if not followee_ids:
        return {
            "statusCode": 200,
            "headers":    HEADERS,
            "body":       json.dumps({"photos": [], "count": 0, "nextCursor": None},
                                     cls=DecimalEncoder),
        }

    # ── Step 2: scan PhotoMetadataTable filtering by those photographers ──────
    # DynamoDB FilterExpression with is_in() accepts up to 100 values — safe
    # for a realistic follow list.  If a user follows > 100 photographers we
    # could paginate the follow list; for MVP 100 is plenty.
    meta_table  = dynamodb.Table(METADATA_TABLE)
    filter_expr = (
        boto3.dynamodb.conditions.Attr("photographerId").is_in(followee_ids)
        & boto3.dynamodb.conditions.Attr("status").ne("flagged")
    )

    scan_kwargs: dict = {"FilterExpression": filter_expr}
    if cursor_str:
        start_key = _decode_cursor(cursor_str)
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key

    scan_resp = meta_table.scan(**scan_kwargs)
    items     = scan_resp.get("Items", [])
    last_key  = scan_resp.get("LastEvaluatedKey")

    # ── Sort newest-first, then paginate ──────────────────────────────────────
    items.sort(
        key=lambda x: x.get("uploadedAt", x.get("uploadDate", "")),
        reverse=True,
    )
    page     = items[:limit]
    last_key = last_key if len(items) > limit else None

    # ── Build response ────────────────────────────────────────────────────────
    photos = []
    for item in page:
        thumb_key   = item.get("thumbnailKey", "")
        preview_key = item.get("previewKey", "")
        photos.append({
            "photoId":          item.get("photoId"),
            "title":            item.get("title", ""),
            "description":      item.get("description", ""),
            "tags":             list(item.get("tags") or []),
            "likes":            int(item.get("likes", 0)),
            "price":            float(item.get("price", 0)),
            "category":         item.get("category", "other"),
            "photographerId":   item.get("photographerId", ""),
            "photographerName": item.get("photographerName", ""),
            "thumbnailUrl":     _thumb_url(thumb_key) if thumb_key else "",
            "previewUrl":       _preview_url(preview_key) if preview_key else "",
            "uploadedAt":       item.get("uploadedAt", item.get("uploadDate", "")),
        })

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps({
            "photos":     photos,
            "count":      len(photos),
            "nextCursor": _encode_cursor(last_key) if last_key else None,
        }, cls=DecimalEncoder),
    }


def _annotate(**kwargs):
    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        if seg:
            for k, v in kwargs.items():
                seg.put_annotation(k, str(v))
    except Exception:
        pass
