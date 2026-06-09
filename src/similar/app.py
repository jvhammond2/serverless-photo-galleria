"""
SimilarPhotosFunction — src/similar/app.py
-------------------------------------------
GET /photos/similar?photoId=abc123
GET /photos/similar?photoId=abc123&limit=6&threshold=10

Returns photos that are perceptually similar to the given photo, ranked by
ascending Hamming distance between their pHash fingerprints.

Algorithm:
  1. Fetch the source photo's pHash from DynamoDB.
  2. Scan PhotoMetadataTable for all active photos that have a pHash.
  3. Compute Hamming distance between each candidate and the source.
  4. Return up to `limit` photos with distance ≤ `threshold`, sorted by distance.

Hamming distance between two 64-bit pHashes:
  - 0       = identical image (or exact duplicate)
  - 1–5     = very likely the same photo (different compression/resize)
  - 6–10    = perceptually similar (same scene, different crop or edit)
  - 11–20   = loosely related (similar subject/colour)
  - > 20    = unrelated

AWS Cert Note (SAA-C03 / DVA-C02):
  This is a full-table Scan — O(n) in the number of photos.  At MVP scale
  (hundreds to low thousands of photos) this is fine and costs pennies.
  At scale the right answer is Locality-Sensitive Hashing (LSH): bucket
  photos by pHash prefix into a GSI so you only compare within the same
  bucket.  Amazon OpenSearch Service also supports kNN vector search which
  can replace this approach entirely.

No auth required — similarity results are public metadata.
"""

import boto3
import json
import os
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")

from aws_xray_sdk.core import patch_all, xray_recorder
patch_all()

METADATA_TABLE          = os.environ["METADATA_TABLE"]
THUMBS_DISTRIBUTION_URL = os.environ["THUMBS_DISTRIBUTION_URL"].rstrip("/")

DEFAULT_LIMIT     = 6
MAX_LIMIT         = 24
DEFAULT_THRESHOLD = 10   # max Hamming distance to be considered "similar"

HEADERS = {
    "Content-Type":                "application/json",
    "Access-Control-Allow-Origin": "*",
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _hamming(h1: str, h2: str) -> int:
    """
    Hamming distance between two hex pHash strings.
    Pure Python — no imagehash import needed for comparison.

    AWS Cert Note (DVA-C02): pHash hex strings are 16 chars = 64 bits.
    XOR the two 64-bit integers then count set bits (popcount).
    bin(n).count('1') is the standard Python popcount idiom.
    """
    if not h1 or not h2 or len(h1) != len(h2):
        return 999
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count("1")
    except ValueError:
        return 999


def _error(status: int, msg: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


def handler(event, context):
    params     = event.get("queryStringParameters") or {}
    photo_id   = (params.get("photoId") or "").strip()
    try:
        limit = min(int(params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)
    except (ValueError, TypeError):
        limit = DEFAULT_LIMIT
    try:
        threshold = int(params.get("threshold", DEFAULT_THRESHOLD))
    except (ValueError, TypeError):
        threshold = DEFAULT_THRESHOLD

    if not photo_id:
        return _error(400, "photoId is required")

    table = dynamodb.Table(METADATA_TABLE)

    # ── Step 1: get the source photo's pHash ─────────────────────────────────
    resp        = table.get_item(Key={"photoId": photo_id})
    source_item = resp.get("Item")
    if not source_item:
        return _error(404, f"Photo '{photo_id}' not found")

    source_hash = (source_item.get("pHash") or "").strip()
    if not source_hash:
        return _error(422, "Source photo has no pHash — it may still be processing")

    _annotate(photoId=photo_id, sourceHash=source_hash, threshold=str(threshold))

    # ── Step 2: scan all active photos that have a pHash ─────────────────────
    from boto3.dynamodb.conditions import Attr
    scan_kwargs = {
        "FilterExpression": (
            Attr("pHash").exists()
            & Attr("pHash").ne("")
            & Attr("status").ne("flagged")
        ),
        "ProjectionExpression": (
            "photoId, pHash, thumbnailKey, title, fileName, "
            "likes, #cat, photographerId, photographerName, uploadedAt"
        ),
        "ExpressionAttributeNames": {"#cat": "category"},
    }

    candidates = []
    while True:
        resp      = table.scan(**scan_kwargs)
        candidates.extend(resp.get("Items", []))
        last_key  = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    # ── Step 3: rank by Hamming distance, exclude the source itself ───────────
    scored = []
    for item in candidates:
        if item["photoId"] == photo_id:
            continue
        dist = _hamming(source_hash, item.get("pHash", ""))
        if dist <= threshold:
            scored.append((dist, item))

    scored.sort(key=lambda x: x[0])

    # ── Step 4: build response ────────────────────────────────────────────────
    photos = []
    for dist, item in scored[:limit]:
        thumb_key = item.get("thumbnailKey", "")
        photos.append({
            "photoId":          item.get("photoId"),
            "title":            item.get("title") or item.get("fileName") or "Untitled",
            "likes":            int(item.get("likes", 0)),
            "category":         item.get("category", "other"),
            "photographerId":   item.get("photographerId", ""),
            "photographerName": item.get("photographerName", ""),
            "thumbnailUrl":     f"{THUMBS_DISTRIBUTION_URL}/{thumb_key}" if thumb_key else "",
            "uploadedAt":       item.get("uploadedAt", ""),
            "hammingDistance":  dist,
        })

    _annotate(resultCount=str(len(photos)))

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps({
            "sourcePhotoId": photo_id,
            "photos":        photos,
            "count":         len(photos),
            "threshold":     threshold,
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
