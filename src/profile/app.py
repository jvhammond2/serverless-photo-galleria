"""
ProfileFunction — handles:
  GET  /profile               (no auth)             — public photographer profile
  PUT  /profile               (PhotographerCognito) — save/update profile
  GET  /admin/flagged         (PhotographerCognito) — list quarantined photos for review
  POST /admin/approve         (PhotographerCognito) — approve a flagged photo (clear status)
  POST /admin/editor-choice   (PhotographerCognito) — toggle editorChoice flag on a photo
"""
import json
import os
import boto3
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Attr

from aws_xray_sdk.core import patch_all, xray_recorder
patch_all()

ddb = boto3.resource("dynamodb")
s3  = boto3.client("s3")

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,PUT,POST,OPTIONS",
}

PROFILE_PK       = "PHOTOGRAPHER_PROFILE"
URL_TTL          = 3600
THUMBS_URL       = os.environ.get("THUMBS_DISTRIBUTION_URL", "").rstrip("/")
THUMBS_BUCKET    = os.environ.get("THUMBS_BUCKET", "")
ORIGINALS_BUCKET = os.environ.get("ORIGINALS_BUCKET", "")


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

    # ── POST /admin/editor-choice ─────────────────────────────────────────
    # Toggle the editorChoice boolean on any photo.  The flag is read by the
    # search Lambda when ?sort=editors_choice is requested.
    #
    # AWS Cert Note (DVA-C02): update_item with SET is a partial write — only
    # the named attribute is changed; every other field is untouched.  This is
    # cheaper and safer than put_item (which would overwrite the whole item).
    if method == "POST" and path.endswith("/admin/editor-choice"):
        try:
            body     = json.loads(event.get("body") or "{}")
            photo_id = body.get("photoId", "").strip()
            enabled  = bool(body.get("editorChoice", False))
        except Exception:
            return _err(400, "Invalid JSON")
        if not photo_id:
            return _err(400, "photoId required")

        table = ddb.Table(os.environ["METADATA_TABLE"])
        table.update_item(
            Key={"photoId": photo_id},
            UpdateExpression="SET editorChoice = :v",
            ExpressionAttributeValues={":v": enabled},
        )
        _annotate(photoId=photo_id, editorChoice=str(enabled))
        return {
            "statusCode": 200,
            "headers":    CORS,
            "body":       json.dumps({"photoId": photo_id, "editorChoice": enabled}),
        }

    # ── POST /photos/metadata ─────────────────────────────────────────────
    # Lightweight metadata update — saves category and/or colorMood directly
    # to DynamoDB without triggering the full image-processing pipeline.
    # This is the right tool for tagging existing photos; re-processing is
    # only needed when the photographer wants to change image adjustments.
    if method == "POST" and path.endswith("/photos/metadata"):
        try:
            body = json.loads(event.get("body") or "{}")
        except Exception:
            return _err(400, "Invalid JSON")

        photo_id = body.get("photoId", "").strip()
        if not photo_id:
            return _err(400, "photoId required")

        VALID_CATEGORIES = {
            "abstract","aerial","animals","bw","boudoir","celebrities","city",
            "commercial","concert","family","fashion","film","fineart","food",
            "journalism","landscape","macro","nature","night","people","performing",
            "sport","stilllife","street","transportation","travel","underwater",
            "urbex","wedding","other",""
        }
        VALID_MOODS = {"warm","cool","green","purple","neutral","dark","light",""}

        update_parts, expr_values, expr_names = [], {}, {}

        category = body.get("category", None)
        if category is not None:
            cat = category.strip().lower()
            if cat not in VALID_CATEGORIES:
                return _err(400, f"Invalid category: {cat}")
            update_parts.append("#cat = :cat")
            expr_names["#cat"] = "category"
            expr_values[":cat"] = cat

        color_mood = body.get("colorMood", None)
        if color_mood is not None:
            mood = color_mood.strip().lower()
            if mood not in VALID_MOODS:
                return _err(400, f"Invalid colorMood: {mood}")
            update_parts.append("colorMood = :mood")
            expr_values[":mood"] = mood

        if not update_parts:
            return _err(400, "Nothing to update — provide category and/or colorMood")

        # Always ensure uploadedAt exists — it's the GSI sort key for category-uploadedAt-index.
        # Photos uploaded before the GSI was added may be missing it.
        now_iso = datetime.now(timezone.utc).isoformat()
        update_parts.append("uploadedAt = if_not_exists(uploadedAt, :_now)")
        expr_values[":_now"] = now_iso

        ddb.Table(os.environ["METADATA_TABLE"]).update_item(
            Key={"photoId": photo_id},
            UpdateExpression="SET " + ", ".join(update_parts),
            ExpressionAttributeValues=expr_values,
            **({"ExpressionAttributeNames": expr_names} if expr_names else {}),
        )
        _annotate(photoId=photo_id, action="metadata_update")
        return {"statusCode": 200, "headers": CORS,
                "body": json.dumps({"photoId": photo_id, "updated": list(body.keys())})}

    # ── GET /profile ──────────────────────────────────────────────────────
    if method == "GET":
        resp    = ddb.Table(os.environ["PROFILE_TABLE"]).get_item(Key={"profileId": PROFILE_PK})
        profile = resp.get("Item", {})
        profile.pop("profileId", None)
        # Build avatarUrl from stored key if not already set
        avatar_key = profile.get("avatarKey", "")
        if avatar_key and THUMBS_URL and "avatarUrl" not in profile:
            profile["avatarUrl"] = f"{THUMBS_URL}/{avatar_key}"
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

        # Grab the photographer's Cognito sub from the validated JWT claims
        # so the customer portal knows which photographerId to use for series queries.
        claims = (event.get("requestContext") or {}).get("authorizer", {}).get("claims", {})
        cognito_sub = claims.get("sub", "")

        item = {
            "profileId":              PROFILE_PK,
            "displayName":            str(body.get("displayName",   ""))[:100],
            "bio":                    str(body.get("bio",           ""))[:3000],
            "location":               str(body.get("location",      ""))[:100],
            "website":                str(body.get("website",       ""))[:200],
            "instagram":              str(body.get("instagram",     ""))[:100],
            "watermarkText":          str(body.get("watermarkText", ""))[:120],
            "equipment":              clean_eq,
            "updatedAt":              datetime.now(timezone.utc).isoformat(),
        }
        if cognito_sub:
            item["photographerCognitoSub"] = cognito_sub
        avatar_key = str(body.get("avatarKey", "")).strip()
        if avatar_key and ORIGINALS_BUCKET and THUMBS_BUCKET:
            # Copy from originals (where browser PUT it) → thumbs (CloudFront-served)
            dest_key = "avatars/avatar.jpg"
            try:
                s3.copy_object(
                    CopySource={"Bucket": ORIGINALS_BUCKET, "Key": avatar_key},
                    Bucket=THUMBS_BUCKET,
                    Key=dest_key,
                    ContentType="image/jpeg",
                    MetadataDirective="REPLACE",
                )
                item["avatarKey"] = dest_key
                if THUMBS_URL:
                    item["avatarUrl"] = f"{THUMBS_URL}/{dest_key}"
            except Exception as e:
                print(f"Avatar copy failed: {e}")
        elif avatar_key:
            item["avatarKey"] = avatar_key

        ddb.Table(os.environ["PROFILE_TABLE"]).put_item(Item=item)
        item.pop("profileId", None)
        return {
            "statusCode": 200,
            "headers":    CORS,
            "body":       json.dumps({"message": "saved", "profile": item}),
        }

    return _err(405, "Method not allowed")


# ── helpers ───────────────────────────────────────────────────────────────────

def _err(code: int, msg: str):
    return {"statusCode": code, "headers": CORS, "body": json.dumps({"error": msg})}


def _annotate(**kwargs):
    """Add key/value annotations to the current X-Ray segment (best-effort)."""
    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        for k, v in kwargs.items():
            seg.put_annotation(k, str(v))
    except Exception:
        pass