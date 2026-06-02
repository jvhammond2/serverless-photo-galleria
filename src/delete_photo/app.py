"""
DeletePhotoFunction — src/delete_photo/app.py
---------------------------------------------
POST /delete-photo
  Body: { "photoId": "<uuid>" }
  Auth: PhotographerCognito (GalleriaUserPool only)

Permanently removes a photo from all three S3 buckets and DynamoDB.
Steps:
  1. Fetch the full DynamoDB record to retrieve all S3 keys.
  2. Delete originals / thumbs / previews objects from S3.
  3. Delete the DynamoDB metadata record.

Any S3 deletion failures are collected as warnings but do not block
the DynamoDB deletion — a partially cleaned record is worse than
orphaned S3 objects (which the Lifecycle policy will eventually purge).
"""

import boto3
import json
import os

dynamodb = boto3.resource("dynamodb")
s3       = boto3.client("s3")

METADATA_TABLE   = os.environ["METADATA_TABLE"]
ORIGINALS_BUCKET = os.environ["ORIGINALS_BUCKET"]
THUMBS_BUCKET    = os.environ["THUMBS_BUCKET"]
PREVIEWS_BUCKET  = os.environ["PREVIEWS_BUCKET"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "headers": HEADERS,
                "body": json.dumps({"error": "Invalid JSON body"})}

    photo_id = (body.get("photoId") or "").strip()
    if not photo_id:
        return {"statusCode": 400, "headers": HEADERS,
                "body": json.dumps({"error": "photoId is required"})}

    table = dynamodb.Table(METADATA_TABLE)

    # Step 1 — fetch metadata to find all S3 keys
    try:
        resp = table.get_item(Key={"photoId": photo_id})
        item = resp.get("Item")
    except Exception as exc:
        return {"statusCode": 500, "headers": HEADERS,
                "body": json.dumps({"error": f"DB lookup failed: {exc}"})}

    if not item:
        return {"statusCode": 404, "headers": HEADERS,
                "body": json.dumps({"error": "Photo not found"})}

    # Step 2 — delete S3 objects (collect warnings, don't abort)
    s3_targets = [
        (ORIGINALS_BUCKET, item.get("originalKey")),
        (THUMBS_BUCKET,    item.get("thumbnailKey")),
        (PREVIEWS_BUCKET,  item.get("previewKey")),
    ]
    warnings = []
    for bucket, key in s3_targets:
        if not key:
            continue
        try:
            s3.delete_object(Bucket=bucket, Key=key)
        except Exception as exc:
            warnings.append(f"s3://{bucket}/{key}: {exc}")

    # Step 3 — delete DynamoDB record
    try:
        table.delete_item(Key={"photoId": photo_id})
    except Exception as exc:
        return {"statusCode": 500, "headers": HEADERS,
                "body": json.dumps({"error": f"DB delete failed: {exc}", "warnings": warnings})}

    return {
        "statusCode": 200,
        "headers": HEADERS,
        "body": json.dumps({"message": "Photo deleted", "warnings": warnings}),
    }
