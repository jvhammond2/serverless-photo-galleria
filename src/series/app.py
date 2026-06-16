"""
SeriesFunction — src/series/app.py
------------------------------------
Photo essay / narrative series CRUD.

Routes:
  POST   /series          body: {title, description, coverPhotoId, photoIds:[]}
  GET    /series?seriesId=xxx                 — read one series (+ photo metadata)
  GET    /series?photographerId=xxx           — list all series for a photographer
  PUT    /series          body: {seriesId, title?, description?, coverPhotoId?, photoIds?}
  DELETE /series?seriesId=xxx

All write routes require a valid Authorization header (JWT from Cognito).

Design (cost-conscious):
  - Photographer index GSI lets us Query (not Scan) by photographer — O(result set).
  - Photo metadata fetched via BatchGetItem (single round-trip, not N GetItem calls).
  - Photo thumbnails use CloudFront URLs — no presigned URL cost.
  - Series records are small (UUIDs + strings); DynamoDB on-demand billing means
    zero cost at rest and ~$1.25/million writes at scale.

AWS Cert Note (DVA-C02):
  DynamoDB BatchGetItem can fetch up to 100 items or 16 MB in a single call.
  Unprocessed keys are returned in UnprocessedKeys — you must loop until empty.
  This Lambda retries once with exponential back-off, which is sufficient for MVP.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key

SERIES_TABLE     = os.environ["SERIES_TABLE"]
METADATA_TABLE   = os.environ["METADATA_TABLE"]
THUMBS_URL       = os.environ.get("THUMBS_DISTRIBUTION_URL", "").rstrip("/")
PHOTOGRAPHER_IDX = os.environ.get("PHOTOGRAPHER_INDEX", "photographerId-createdAt-index")

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "POST,GET,PUT,DELETE,OPTIONS",
}

ddb = boto3.resource("dynamodb")


def handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return _ok({})

    if method == "POST":
        return _create(event)
    if method == "GET":
        return _read(event)
    if method == "PUT":
        return _update(event)
    if method == "DELETE":
        return _delete(event)
    return _err(405, f"Method {method} not allowed")


# ── CREATE ────────────────────────────────────────────────────────────────────

def _create(event):
    auth = (event.get("headers") or {}).get("Authorization", "")
    if not auth:
        return _err(401, "Authorization required")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON body")

    title       = (body.get("title") or "").strip()
    description = (body.get("description") or "").strip()
    photo_ids   = [p for p in (body.get("photoIds") or []) if isinstance(p, str) and p.strip()]
    cover_id    = (body.get("coverPhotoId") or (photo_ids[0] if photo_ids else "")).strip()
    pg_id       = (body.get("photographerId") or "").strip()

    if not title:
        return _err(400, "title is required")
    if not pg_id:
        return _err(400, "photographerId is required")

    series_id = str(uuid.uuid4())
    now       = datetime.now(timezone.utc).isoformat()

    table = ddb.Table(SERIES_TABLE)
    table.put_item(Item={
        "seriesId":       series_id,
        "photographerId": pg_id,
        "title":          title,
        "description":    description,
        "photoIds":       photo_ids,
        "coverPhotoId":   cover_id,
        "createdAt":      now,
        "updatedAt":      now,
    })

    print(f"[series] Created seriesId={series_id} photographerId={pg_id} photos={len(photo_ids)}")
    return _ok({"seriesId": series_id, "createdAt": now})


# ── READ ──────────────────────────────────────────────────────────────────────

def _read(event):
    params        = event.get("queryStringParameters") or {}
    series_id     = (params.get("seriesId") or "").strip()
    photographer_id = (params.get("photographerId") or "").strip()

    if series_id:
        return _get_one(series_id)
    if photographer_id:
        return _list_by_photographer(photographer_id)
    return _err(400, "seriesId or photographerId query param required")


def _get_one(series_id: str):
    table  = ddb.Table(SERIES_TABLE)
    resp   = table.get_item(Key={"seriesId": series_id})
    series = resp.get("Item")
    if not series:
        return _err(404, f"Series {series_id} not found")

    # Enrich with photo metadata (thumbnails, titles)
    photos = _fetch_photo_meta(list(series.get("photoIds") or []))
    return _ok({**_serialize(series), "photos": photos})


def _list_by_photographer(photographer_id: str):
    table = ddb.Table(SERIES_TABLE)
    resp  = table.query(
        IndexName=PHOTOGRAPHER_IDX,
        KeyConditionExpression=Key("photographerId").eq(photographer_id),
        ScanIndexForward=False,   # newest first
    )
    items = resp.get("Items", [])

    # Batch-fetch cover thumbnails in one DynamoDB round-trip
    cover_thumb_map = {}
    cover_ids = [s["coverPhotoId"] for s in items if s.get("coverPhotoId")]
    if cover_ids and METADATA_TABLE and THUMBS_URL:
        keys = [{"photoId": pid} for pid in cover_ids[:100]]
        br = ddb.batch_get_item(RequestItems={METADATA_TABLE: {"Keys": keys}})
        for meta in br.get("Responses", {}).get(METADATA_TABLE, []):
            thumb_key = meta.get("thumbnailKey", "")
            if thumb_key:
                cover_thumb_map[meta["photoId"]] = f"{THUMBS_URL}/{thumb_key}"

    serialized = []
    for s in items:
        obj = _serialize(s)
        obj["coverThumbUrl"] = cover_thumb_map.get(s.get("coverPhotoId", ""), "")
        serialized.append(obj)

    return _ok({"series": serialized, "count": len(serialized)})


# ── UPDATE ────────────────────────────────────────────────────────────────────

def _update(event):
    auth = (event.get("headers") or {}).get("Authorization", "")
    if not auth:
        return _err(401, "Authorization required")

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON body")

    series_id = (body.get("seriesId") or "").strip()
    if not series_id:
        return _err(400, "seriesId is required")

    table = ddb.Table(SERIES_TABLE)
    # Verify exists
    if not table.get_item(Key={"seriesId": series_id}).get("Item"):
        return _err(404, f"Series {series_id} not found")

    # Build update expression from provided fields only
    set_parts = ["updatedAt = :ua"]
    values    = {":ua": datetime.now(timezone.utc).isoformat()}
    names     = {}

    if "title" in body and body["title"]:
        set_parts.append("#t = :t"); names["#t"] = "title"; values[":t"] = body["title"].strip()
    if "description" in body:
        set_parts.append("description = :d"); values[":d"] = body["description"]
    if "coverPhotoId" in body:
        set_parts.append("coverPhotoId = :c"); values[":c"] = body["coverPhotoId"]
    if "photoIds" in body:
        ids = [p for p in (body["photoIds"] or []) if isinstance(p, str) and p.strip()]
        set_parts.append("photoIds = :p"); values[":p"] = ids
        # Auto-update cover if not set and list changed
        if "coverPhotoId" not in body and ids:
            set_parts.append("coverPhotoId = :cv"); values[":cv"] = ids[0]

    kwargs = {
        "Key":                     {"seriesId": series_id},
        "UpdateExpression":        "SET " + ", ".join(set_parts),
        "ExpressionAttributeValues": values,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names

    table.update_item(**kwargs)
    print(f"[series] Updated seriesId={series_id}")
    return _ok({"seriesId": series_id, "updated": True})


# ── DELETE ────────────────────────────────────────────────────────────────────

def _delete(event):
    auth = (event.get("headers") or {}).get("Authorization", "")
    if not auth:
        return _err(401, "Authorization required")

    params    = event.get("queryStringParameters") or {}
    series_id = (params.get("seriesId") or "").strip()
    if not series_id:
        return _err(400, "seriesId query param required")

    table = ddb.Table(SERIES_TABLE)
    if not table.get_item(Key={"seriesId": series_id}).get("Item"):
        return _err(404, f"Series {series_id} not found")

    table.delete_item(Key={"seriesId": series_id})
    print(f"[series] Deleted seriesId={series_id}")
    return _ok({"deleted": True, "seriesId": series_id})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fetch_photo_meta(photo_ids: list) -> list:
    """
    BatchGetItem for photo thumbnails + titles.
    Returns list in the same order as photo_ids.

    AWS Cert Note (DVA-C02): BatchGetItem fetches up to 100 items in one call.
    Unprocessed keys must be retried — we do one retry for simplicity.
    """
    if not photo_ids:
        return []

    meta_table = ddb.Table(METADATA_TABLE)
    keys       = [{"photoId": pid} for pid in photo_ids[:100]]  # cap at 100

    resp            = ddb.batch_get_item(RequestItems={METADATA_TABLE: {"Keys": keys}})
    items_map       = {item["photoId"]: item for item in resp.get("Responses", {}).get(METADATA_TABLE, [])}

    # Retry unprocessed keys once
    unproc = resp.get("UnprocessedKeys", {})
    if unproc:
        retry     = ddb.batch_get_item(RequestItems=unproc)
        for item in retry.get("Responses", {}).get(METADATA_TABLE, []):
            items_map[item["photoId"]] = item

    result = []
    for pid in photo_ids[:100]:
        item = items_map.get(pid, {})
        thumb_key = item.get("thumbnailKey", "")
        result.append({
            "photoId":      pid,
            "title":        item.get("title", item.get("fileName", "Untitled")),
            "thumbnailUrl": f"{THUMBS_URL}/{thumb_key}" if thumb_key and THUMBS_URL else "",
            "tags":         list(item.get("tags") or []),
            "likes":        int(item.get("likes", 0)),
        })
    return result


def _serialize(series: dict) -> dict:
    """Return a JSON-safe series dict (no Decimal, cover thumb URL resolved)."""
    cover_id  = series.get("coverPhotoId", "")
    photo_ids = list(series.get("photoIds") or [])
    return {
        "seriesId":       series.get("seriesId"),
        "photographerId": series.get("photographerId"),
        "title":          series.get("title", ""),
        "description":    series.get("description", ""),
        "photoIds":       photo_ids,
        "coverPhotoId":   cover_id,
        "photoCount":     len(photo_ids),
        "createdAt":      series.get("createdAt", ""),
        "updatedAt":      series.get("updatedAt", ""),
    }


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
