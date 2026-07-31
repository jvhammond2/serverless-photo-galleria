"""
GetUploadUrlFunction — src/get_upload_url/app.py
-------------------------------------------------
POST /upload-url
  Body: { "fileName": "my-photo.jpg", "fileType": "image/jpeg" }
  Auth: PhotographerCognito

Returns a pre-signed S3 PUT URL valid for 300 seconds so the browser
can upload directly to the originals bucket without routing bytes
through Lambda.

Security:
  - File extension validated against ALLOWED_EXTENSIONS allowlist.
  - File type (MIME) validated against ALLOWED_TYPES allowlist.
  - S3 key is UUID-based (server-generated), not caller-controlled,
    preventing path traversal and filename collision attacks.
  - Errors return generic messages; details are logged server-side only.

AWS Cert Note (SAA-C03):
  Pre-signed PUT URLs delegate s3:PutObject to the bearer for a fixed
  TTL using the Lambda's IAM role credentials. The upload goes directly
  client→S3 — Lambda is not in the data path and pays no egress cost.
"""

import json
import os
import uuid

import boto3

s3_client = boto3.client("s3")

ORIGINALS_BUCKET  = os.environ.get("ORIGINALS_BUCKET")
ALLOWED_ORIGIN    = os.environ.get("ALLOWED_ORIGIN", "*")
UPLOAD_TTL        = 300  # seconds

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".tif"}
ALLOWED_TYPES      = {"image/jpeg", "image/png", "image/webp", "image/tiff"}

HEADERS = {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Content-Type": "application/json",
}


def _photographer_id(event):
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email") or "unknown"


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": HEADERS, "body": ""}

    try:
        body = event.get("body") or "{}"
        if isinstance(body, str):
            body = json.loads(body)

        file_name = (body.get("fileName") or "").strip()
        file_type = (body.get("fileType") or "").strip().lower()

        if not file_name or not file_type:
            return {"statusCode": 400, "headers": HEADERS,
                    "body": json.dumps({"error": "fileName and fileType are required"})}

        # Validate file extension
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            return {"statusCode": 400, "headers": HEADERS,
                    "body": json.dumps({"error": f"File type not allowed. Accepted: JPEG, PNG, WebP, TIFF"})}

        # Validate MIME type
        if file_type not in ALLOWED_TYPES:
            return {"statusCode": 400, "headers": HEADERS,
                    "body": json.dumps({"error": "Content type not allowed"})}

        # Build a safe S3 key: {photographerId}/{uuid}{ext}
        # Never use the caller-supplied file_name as the key directly
        photographer_id = _photographer_id(event)
        safe_key = f"{photographer_id}/{uuid.uuid4()}{ext}"

        presigned_url = s3_client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket":      ORIGINALS_BUCKET,
                "Key":         safe_key,
                "ContentType": file_type,
            },
            ExpiresIn=UPLOAD_TTL,
        )

        print(f"[upload-url] presign issued: photographer={photographer_id} key={safe_key}")

        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({"uploadUrl": presigned_url, "s3Key": safe_key}),
        }

    except Exception as e:
        print(f"[upload-url] Error: {e}")
        return {
            "statusCode": 500,
            "headers": HEADERS,
            "body": json.dumps({"error": "Failed to generate upload URL"}),
        }