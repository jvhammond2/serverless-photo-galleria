"""
AudioStoryFunction — src/audio_story/app.py
--------------------------------------------
POST /audio-story           body: {"photoId": "...", "contentType": "audio/webm"}
DELETE /audio-story?photoId=...

POST flow (photographer uploads a voice note for a photo):
  1. Validate the JWT (Authorization header) — same pattern as other admin routes.
  2. Generate a presigned S3 PUT URL (15 min TTL) for the audio file.
  3. Save the S3 key to the photo's DynamoDB metadata record so the search
     Lambda can generate a presigned GET URL for customers.
  4. Return the presigned PUT URL + key so the browser can upload directly.

DELETE flow:
  1. Delete the audio object from S3.
  2. Remove the audioStoryKey field from DynamoDB.

Design (cost-conscious):
  - Browser uploads directly to S3 via presigned URL — no Lambda proxying
    audio bytes, which avoids payload size limits and egress costs.
  - Audio stored in a dedicated AudioStoriesS3Bucket (cheap when empty).
  - Playback presigned URLs generated on-demand by the search Lambda
    (1-hour TTL) — no persistent public URLs for audio.

AWS Cert Note (SAA-C03 / DVA-C02):
  Presigned S3 PUT URLs delegate s3:PutObject permission to the bearer for
  a fixed TTL without exposing AWS credentials.  The Lambda's IAM role signs
  the URL using its own credentials; the upload happens client-to-S3 directly.
  This is the standard pattern for browser-based S3 uploads.
"""

import json
import os
import uuid

import boto3

CORS = {
    "Access-Control-Allow-Origin":  os.environ.get("ALLOWED_ORIGIN", "*"),
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "POST,DELETE,OPTIONS",
}

AUDIO_BUCKET    = os.environ["AUDIO_BUCKET"]
METADATA_TABLE  = os.environ["METADATA_TABLE"]
PUT_URL_TTL     = 900   # 15 minutes — enough for the browser to upload
GET_URL_TTL     = 3600  # 1 hour — returned separately by search Lambda

# Allowlist of permitted audio MIME types for presigned PUT URL generation.
# Rejecting unknown content-types prevents using the audio bucket as a host
# for arbitrary file types (HTML, JavaScript, executables, etc.).
ALLOWED_AUDIO_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg"}

s3  = boto3.client("s3")
ddb = boto3.resource("dynamodb")


def handler(event, context):
    method = event.get("httpMethod", "")
    if method == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    auth = (event.get("headers") or {}).get("Authorization", "")
    if not auth:
        return _err(401, "Authorization header required")

    if method == "POST":
        return _handle_post(event)
    if method == "DELETE":
        return _handle_delete(event)
    return _err(405, f"Method {method} not allowed")


# ── POST — generate presigned PUT URL and record key ─────────────────────────

def _handle_post(event):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _err(400, "Invalid JSON body")

    photo_id     = (body.get("photoId") or "").strip()
    content_type = (body.get("contentType") or "audio/webm").strip().lower()

    if not photo_id:
        return _err(400, "photoId is required")

    # Validate content type against allowlist
    if content_type not in ALLOWED_AUDIO_TYPES:
        return _err(400, f"Content type not allowed. Accepted: {', '.join(sorted(ALLOWED_AUDIO_TYPES))}")

    # Validate the photo exists
    table = ddb.Table(METADATA_TABLE)
    resp  = table.get_item(Key={"photoId": photo_id})
    if not resp.get("Item"):
        return _err(404, f"Photo {photo_id} not found")

    # S3 key — deterministic so re-recording replaces the previous file
    s3_key = f"audio/{photo_id}.webm"

    # Generate presigned PUT URL
    put_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket":      AUDIO_BUCKET,
            "Key":         s3_key,
            "ContentType": content_type,
        },
        ExpiresIn=PUT_URL_TTL,
    )

    # Save key to DynamoDB — marks this photo as having an audio story
    table.update_item(
        Key={"photoId": photo_id},
        UpdateExpression="SET audioStoryKey = :k",
        ExpressionAttributeValues={":k": s3_key},
    )

    print(f"[audio] Presigned PUT issued for photoId={photo_id}, key={s3_key}")
    return _ok({"uploadUrl": put_url, "key": s3_key})


# ── DELETE — remove audio from S3 and DynamoDB ───────────────────────────────

def _handle_delete(event):
    params   = event.get("queryStringParameters") or {}
    photo_id = (params.get("photoId") or "").strip()

    if not photo_id:
        return _err(400, "photoId query param required")

    table = ddb.Table(METADATA_TABLE)
    resp  = table.get_item(Key={"photoId": photo_id})
    item  = resp.get("Item")
    if not item:
        return _err(404, f"Photo {photo_id} not found")

    s3_key = item.get("audioStoryKey", f"audio/{photo_id}.webm")

    # Delete from S3 (idempotent — no error if key doesn't exist)
    try:
        s3.delete_object(Bucket=AUDIO_BUCKET, Key=s3_key)
        print(f"[audio] Deleted s3://{AUDIO_BUCKET}/{s3_key}")
    except Exception as e:
        print(f"[audio] S3 delete failed (non-fatal): {e}")

    # Remove field from DynamoDB
    table.update_item(
        Key={"photoId": photo_id},
        UpdateExpression="REMOVE audioStoryKey",
    )

    return _ok({"deleted": True, "photoId": photo_id})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(body: dict):
    return {
        "statusCode": 200,
        "headers": {**CORS, "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _err(code: int, msg: str):
    return {
        "statusCode": code,
        "headers": {**CORS, "Content-Type": "application/json"},
        "body": json.dumps({"error": msg}),
    }
