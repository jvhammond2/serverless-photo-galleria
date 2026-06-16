"""
GetUploadUrlFunction — src/upload_url/app.py
---------------------------------------------
POST /get-upload-url
  Body: { "fileName": "sunset.jpg", "fileType": "image/jpeg" }
        { "type": "avatar" }   — uploads profile photo to thumbs bucket

Returns a presigned S3 PUT URL valid for 5 minutes.
The browser uploads directly to S3 — API Gateway never touches the file bytes.

AWS Best Practice: presigned URLs keep your Lambda/API Gateway layer thin.
Large files bypass the 10 MB API Gateway payload limit entirely, and you
pay S3 data-in pricing instead of API Gateway pricing per request.

Security: The URL is cryptographically signed by AWS STS and scoped to
PUT on one specific key in the originals bucket only. It cannot be used
to read, list, or delete objects.
"""

import boto3
import json
import os
import uuid

s3 = boto3.client("s3")
ORIGINALS_BUCKET = os.environ["ORIGINALS_BUCKET"]
THUMBS_BUCKET    = os.environ.get("THUMBS_BUCKET", "")

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

URL_EXPIRY_SECONDS = 300  # 5 minutes — enough for any upload, short enough to limit abuse


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "headers": HEADERS, "body": json.dumps({"error": "Invalid JSON body"})}

    file_name   = body.get("fileName", "").strip()
    file_type   = body.get("fileType", "image/jpeg").strip()
    upload_type = body.get("type", "photo").strip()  # "photo" | "avatar"

    # Avatar uploads go to originals bucket first; PUT /profile copies to thumbs bucket
    # so CloudFront can serve them. (Thumbs bucket has OAC policy — no direct PUT allowed.)
    if upload_type == "avatar":
        s3_key    = "avatars/avatar.jpg"
        bucket    = ORIGINALS_BUCKET
        file_type = "image/jpeg"
    else:
        # Sanitise the filename — strip directory traversal attempts
        file_name = os.path.basename(file_name) if file_name else f"{uuid.uuid4()}.jpg"

        # Allowed image MIME types
        allowed_types = {"image/jpeg", "image/png", "image/webp", "image/tiff"}
        if file_type not in allowed_types:
            return {
                "statusCode": 400,
                "headers": HEADERS,
                "body": json.dumps({"error": f"Unsupported file type: {file_type}"}),
            }
        s3_key = file_name
        bucket = ORIGINALS_BUCKET

    upload_url = s3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": bucket,
            "Key": s3_key,
            "ContentType": file_type,
        },
        ExpiresIn=URL_EXPIRY_SECONDS,
    )

    return {
        "statusCode": 200,
        "headers": HEADERS,
        "body": json.dumps({"uploadUrl": upload_url, "s3Key": s3_key}),
    }
