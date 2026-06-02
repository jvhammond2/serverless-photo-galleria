"""
SearchImagesFunction — src/search/app.py
------------------------------------------
GET /list-images
GET /list-images?tag=sunset
GET /list-images?q=golden+hour+Dublin        ← semantic search via Bedrock
GET /list-images?limit=24&cursor=eyJwaG90b...  ← cursor-based pagination
GET /list-images?photographerId=abc123        ← filter by photographer

Query parameters:
  tag            Filter by a Rekognition label tag (exact match, case-insensitive)
  q              Natural-language semantic search using Titan Embeddings.
                 If present, overrides tag filtering.
  limit          Page size (default 24, max 100)
  cursor         Opaque base64-encoded DynamoDB ExclusiveStartKey for next page
  photographerId Filter by photographer

Response:
  {
    "photos": [
      {
        "photoId":      "...",
        "title":        "...",
        "tags":         ["sunset", "ocean"],
        "likes":        12,
        "price":        9.99,
        "thumbnailUrl": "https://d1234.cloudfront.net/thumbs/abc.jpg",
        "previewUrl":   "https://presigned...",    ← 5-min presigned URL
        "photographerId": "...",
        "photographerName": "..."
      }
    ],
    "nextCursor": "eyJwaG90b...",   ← null when no more pages
    "count": 24
  }

Thumbnail URLs use the CloudFront distribution (fast, free egress from CF)
rather than presigned S3 URLs.  Preview URLs remain presigned because
FullSizePreviewsS3Bucket is private and should require a click-through.
"""

import base64
import boto3
import json
import os
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")
s3       = boto3.client("s3")
bedrock  = boto3.client("bedrock-runtime")

METADATA_TABLE          = os.environ["METADATA_TABLE"]
THUMBS_BUCKET           = os.environ["THUMBS_BUCKET"]
PREVIEWS_BUCKET         = os.environ["PREVIEWS_BUCKET"]
THUMBS_DISTRIBUTION_URL = os.environ["THUMBS_DISTRIBUTION_URL"].rstrip("/")
DEFAULT_PAGE_SIZE       = int(os.environ.get("PAGE_SIZE", "24"))
MAX_PAGE_SIZE           = 100

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

PREVIEW_TTL = 300   # 5 minutes


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _error(status: int, msg: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


def _encode_cursor(last_key: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(last_key).encode()).decode()


def _decode_cursor(cursor: str) -> dict | None:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception:
        return None


def _thumb_url(key: str) -> str:
    """CloudFront URL for a thumbnail — no signing needed."""
    return f"{THUMBS_DISTRIBUTION_URL}/{key}"


def _preview_url(key: str) -> str:
    """Short-lived presigned URL for the full-size preview."""
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": PREVIEWS_BUCKET, "Key": key},
        ExpiresIn=PREVIEW_TTL,
    )


def _embed_query(text: str) -> list:
    """Embed a natural-language query using Amazon Titan Embeddings v2."""
    resp = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0",
        body=json.dumps({"inputText": text, "dimensions": 256}),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def _cosine_similarity(a: list, b: list) -> float:
    dot   = sum(x * y for x, y in zip(a, b))
    mag_a = sum(x * x for x in a) ** 0.5
    mag_b = sum(x * x for x in b) ** 0.5
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def handler(event, context):
    params           = event.get("queryStringParameters") or {}
    tag_filter       = (params.get("tag") or "").strip().lower()
    query_text       = (params.get("q") or "").strip()
    photographer_id  = (params.get("photographerId") or "").strip()
    cursor_str       = (params.get("cursor") or "").strip()

    try:
        limit = min(int(params.get("limit", DEFAULT_PAGE_SIZE)), MAX_PAGE_SIZE)
    except (ValueError, TypeError):
        limit = DEFAULT_PAGE_SIZE

    # ── Build DynamoDB filter ─────────────────────────────────────────────
    filter_expr = boto3.dynamodb.conditions.Attr("status").ne("flagged")

    if photographer_id:
        filter_expr = filter_expr & boto3.dynamodb.conditions.Attr("photographerId").eq(photographer_id)

    if tag_filter:
        filter_expr = filter_expr & boto3.dynamodb.conditions.Attr("tags").contains(tag_filter)

    # ── Optionally embed the query for semantic re-ranking ────────────────
    query_embedding = None
    if query_text:
        try:
            query_embedding = _embed_query(query_text)
        except Exception:
            pass  # degrade gracefully to keyword/tag scan

    # ── Paginated DynamoDB scan ───────────────────────────────────────────
    scan_kwargs = {"FilterExpression": filter_expr}

    if cursor_str and not query_embedding:
        start_key = _decode_cursor(cursor_str)
        if start_key:
            scan_kwargs["ExclusiveStartKey"] = start_key

    # Load more candidates for semantic re-ranking; honour limit for regular scan
    scan_kwargs["Limit"] = limit if not query_embedding else min(limit * 10, 500)

    table    = dynamodb.Table(METADATA_TABLE)
    resp     = table.scan(**scan_kwargs)
    items    = resp.get("Items", [])
    last_key = resp.get("LastEvaluatedKey")

    # ── Re-rank by cosine similarity if query embedding was generated ─────
    if query_embedding and items:
        scored = []
        for item in items:
            embedding = item.get("embedding")
            score = _cosine_similarity(query_embedding, embedding) if embedding else 0.0
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        items    = [item for _, item in scored[:limit]]
        last_key = None  # cursor not compatible with in-memory re-ranking

    # ── Build response ────────────────────────────────────────────────────
    photos = []
    for item in items[:limit]:
        thumb_key   = item.get("thumbnailKey", "")
        preview_key = item.get("previewKey", "")
        photos.append({
            "photoId":          item.get("photoId"),
            "title":            item.get("title", ""),
            "description":      item.get("description", ""),
            "tags":             list(item.get("tags") or []),
            "likes":            int(item.get("likes", 0)),
            "price":            float(item.get("price", 0)),
            "photographerId":   item.get("photographerId", ""),
            "photographerName": item.get("photographerName", ""),
            "thumbnailUrl":     _thumb_url(thumb_key) if thumb_key else "",
            "previewUrl":       _preview_url(preview_key) if preview_key else "",
            "createdAt":        item.get("createdAt", ""),
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
