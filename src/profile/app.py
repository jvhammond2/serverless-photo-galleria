"""
ProfileFunction — handles:
  GET  /profile        (no auth)             — public photographer profile
  PUT  /profile        (PhotographerCognito) — save/update profile
  GET  /admin/flagged  (PhotographerCognito) — list quarantined photos for review
  POST /admin/approve  (PhotographerCognito) — approve a flagged photo (clear status)
"""
import json
import os
import boto3
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Attr

ddb = boto3.resource("dynamodb")
s3  = boto3.client("s3")

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,PUT,POST,OPTIONS",
}

PROFILE_PK = "PHOTOGRAPHER_PROFILE"
URL_TTL    = 3600


def handler(event, context):
    method = event.get("httpMethod", "GET")
    path   = event.get("path", "")

    if method == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    # ── GET /admin/flagged ────────────────────────────────────────────────
    if method == "GET" and path.endswith("/admin/flagged"):
        table  = ddb.Table(os.environ["METADATA_TABLE"])
        resp   = table.scan(
            FilterExpression=Attr("moderationStatus").eq("flagged")
        )
        items  = resp.get("Items", [])

        thumbs_bucket = os.environ.get("THUMBS_BUCKET", "")
        photos = []
        for item in items:
            thumb_url = ""
            thumb_key = item.get("thumbnailKey", "")
            if thumb_key and thumbs_bucket:
                try:
                    thumb_url = s3.generate_presigned_url(
                        "get_object",
                        Params={"Bucket": thumbs_bucket, "Key": thumb_key},
                        ExpiresIn=URL_TTL,
                    )
                except Exception:
                    pass
            photos.append({
                "photoId":          item["photoId"],
                "fileName":         item.get("fileName", ""),
                "moderationLabels": item.get("moderationLabels", "[]"),
                "uploadDate":       item.get("uploadDate", ""),
                "thumbnailUrl":     thumb_url,
            })

        photos.sort(key=lambda p: p["uploadDate"], reverse=True)
        return {"statusCode": 200, "headers": CORS, "body": json.dumps(photos)}

    # ── POST /admin/approve ───────────────────────────────────────────────
    if method == "POST" and path.endswith("/admin/approve"):
        try:
            body     = json.loads(event.get("body") or "{}")
            photo_id = body.get("photoId", "").strip()
        except Exception:
            return _err(400, "Invalid JSON")
        if not photo_id:
            return _err(400, "photoId required")

        table = ddb.Table(os.environ["METADATA_TABLE"])
        table.update_item(
            Key={"photoId": photo_id},
            UpdateExpression="SET moderationStatus = :c",
            ExpressionAttributeValues={":c": "clean"},
        )
        return {"statusCode": 200, "headers": CORS, "body": json.dumps({"approved": True})}

    # ── GET /profile ──────────────────────────────────────────────────────
    if method == "GET":
        resp    = ddb.Table(os.environ["PROFILE_TABLE"]).get_item(Key={"profileId": PROFILE_PK})
        profile = resp.get("Item", {})
        profile.pop("profileId", None)
        return {"statusCode": 200, "headers": CORS, "body": json.dumps(profile or {})}

    # ── PUT /profile ──────────────────────────────────────────────────────
    if method == "PUT":
        try:
            body = json.loads(event.get("body") or "{}")
        except Exception:
            return _err(400, "Invalid JSON")

        equipment = body.get("equipment", [])
        if not isinstance(equipment, list):
            return _err(400, "equipment must be an array")
        clean_eq = [{
            "type":  str(eq.get("type",  ""))[:50],
            "brand": str(eq.get("brand", ""))[:100],
            "model": str(eq.get("model", ""))[:100],
            "notes": str(eq.get("notes", ""))[:200],
        } for eq in equipment[:20] if isinstance(eq, dict)]

        item = {
            "profileId":     PROFILE_PK,
            "displayName":   str(body.get("displayName",   ""))[:100],
            "bio":           str(body.get("bio",           ""))[:3000],
            "location":      str(body.get("location",      ""))[:100],
            "website":       str(body.get("website",       ""))[:200],
            "instagram":     str(body.get("instagram",     ""))[:100],
            "watermarkText": str(body.get("watermarkText", ""))[:120],
            "equipment":     clean_eq,
            "updatedAt":     datetime.now(timezone.utc).isoformat(),
        }
        ddb.Table(os.environ["PROFILE_TABLE"]).put_item(Item=item)
        item.pop("profileId", None)
        return {"statusCode": 200, "headers": CORS, "body": json.dumps({"message": "saved", "profile": item})}

    return _err(405, "Method not allowed")


def _err(code, msg):
    return {"statusCode": code, "headers": CORS, "body": json.dumps({"error": msg})}
