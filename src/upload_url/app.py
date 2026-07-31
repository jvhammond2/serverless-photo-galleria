"""
GetUploadUrlFunction — src/upload_url/app.py
---------------------------------------------
POST /get-upload-url
  Body: { "fileName": "sunset.jpg", "fileType": "image/jpeg" }
        { "type": "avatar" }   — uploads profile photo to originals bucket

Returns a presigned S3 PUT URL valid for 5 minutes.
The browser uploads directly to S3 — API Gateway never touches the file bytes.

AWS Best Practice: presigned URLs keep your Lambda/API Gateway layer thin.
Large files bypass the 10 MB API Gateway payload limit entirely, and you
pay S3 data-in pricing instead of API Gateway pricing per request.

Security:
  - S3 key is server-generated (UUID-based) for photo uploads — the caller-supplied
    filename is only used to extract the file extension, never as the S3 key itself.
    This prevents path traversal and filename collision between photographers.
  - Avatar key is scoped to the photographer's Cognito sub so each photographer
    has their own avatar object and cannot overwrite another's.
  - File extension and MIME type are validated against explicit allowlists.
  - Errors return generic messages; AWS SDK details are logged server-side only.
  - CORS origin is configurable via ALLOWED_ORIGIN env var (defaults to * if unset).

The URL is cryptographically signed by AWS STS and scoped to PUT on one
specific key — it cannot be used to read, list, or delete objects.
"""

import boto3
import json
import os
import uuid

s3 = boto3.client("s3")
ORIGINALS_BUCKET   = os.environ["ORIGINALS_BUCKET"]
THUMBS_BUCKET      = os.environ.get("THUMBS_BUCKET", "")
ALLOWED_ORIGIN     = os.environ.get("ALLOWED_ORIGIN", "*")

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
}

URL_EXPIRY_SECONDS = 300  # 5 minutes — enough for any upload, short enough to limit abuse

ALLOWED_TYPES      = {"image/jpeg", "image/png", "image/webp", "image/tiff"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif"}


def _photographer_id(event):
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email") or "unknown"


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": HEADERS, "body": ""}

    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "headers": HEADERS, "body": json.dumps({"error": "Invalid JSON body"})}

    file_name   = (body.get("fileName") or "").strip()
    file_type   = (body.get("fileType") or "image/jpeg").strip().lower()
    upload_type = (body.get("type") or "photo").strip()  # "photo" | "avatar"

    photographer_id = _photographer_id(event)

    if upload_type == "avatar":
        # Avatar key scoped to photographer — prevents one photographer overwriting another's avatar
        s3_key    = f"avatars/{photographer_id}.jpg"
        bucket    = ORIGINALS_BUCKET
        file_type = "image/jpeg"
    else:
        # Validate MIME type
        if file_type not in ALLOWED_TYPES:
            return {
                "statusCode": 400,
                "headers": HEADERS,
                "body": json.dumps({"error": "File type not allowed. Accepted: JPEG, PNG, WebP, TIFF"}),
            }

        # Extract extension from the caller-supplied name (for S3 key only — never use name as key)
        ext = os.path.splitext(file_name)[1].lower() if file_name else ""
        if ext not in ALLOWED_EXTENSIONS:
            ext = ".jpg"  # fall back to .jpg when extension is missing or unrecognised

        # Server-generated UUID key — prevents path traversal and filename collisions
        s3_key = f"{photographer_id}/{uuid.uuid4()}{ext}"
        bucket = ORIGINALS_BUCKET

    try:
        upload_url = s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": bucket,
                "Key": s3_key,
                "ContentType": file_type,
            },
            ExpiresIn=URL_EXPIRY_SECONDS,
        )
    except Exception as e:
        print(f"[upload-url] presign error: {e}")
        return {"statusCode": 500, "headers": HEADERS,
                "body": json.dumps({"error": "Failed to generate upload URL"})}

    print(f"[upload-url] issued: type={upload_type} photographer={photographer_id} key={s3_key}")

    return {
        "statusCode": 200,
        "headers": HEADERS,
        "body": json.dumps({"uploadUrl": upload_url, "s3Key": s3_key}),
    }
