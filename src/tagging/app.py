"""
TaggingFunction — src/tagging/app.py
-------------------------------------
Invoked by the Step Functions photo pipeline as its final stage.

Expected input event (passed from previous pipeline steps):
{
    "photoId":      "uuid-string",
    "originalKey":  "originals/uuid.jpg",
    "thumbnailKey": "thumbs/uuid.jpg",
    "previewKey":   "previews/uuid.jpg"
}

Responsibilities:
  1. Run Amazon Rekognition DetectLabels on the original image.
  2. Write a complete metadata record to PhotoMetadataTable so the
     customer-facing search / gallery endpoints have everything they need.
  3. Return the enriched event so Step Functions can log / chain further.
"""

import boto3
import json
import os
from datetime import datetime, timezone

rekognition = boto3.client("rekognition")
dynamodb    = boto3.resource("dynamodb")

METADATA_TABLE   = os.environ["METADATA_TABLE"]
ORIGINALS_BUCKET = os.environ["ORIGINALS_BUCKET"]   # raw photographer uploads
THUMBS_BUCKET    = os.environ["THUMBS_BUCKET"]       # thumbnails (kept for future use)


def handler(event, context):
    photo_id        = event["photoId"]
    original_key    = event["originalKey"]
    thumbnail_key   = event["thumbnailKey"]
    preview_key     = event["previewKey"]
    photographer_id = event.get("photographerId", "")
    file_name       = original_key.rsplit("/", 1)[-1]

    # ── 1. Rekognition label detection ───────────────────────────────────────
    reko_resp = rekognition.detect_labels(
        Image={"S3Object": {"Bucket": ORIGINALS_BUCKET, "Name": original_key}},
        MaxLabels=25,
        MinConfidence=70,
    )

    tags = [label["Name"].lower() for label in reko_resp.get("Labels", [])]

    # ── 2. Write metadata record ──────────────────────────────────────────────
    table = dynamodb.Table(METADATA_TABLE)
    table.put_item(
        Item={
            "photoId":        photo_id,
            "originalKey":    original_key,
            "thumbnailKey":   thumbnail_key,
            "previewKey":     preview_key,
            "tags":           tags,
            "likes":          0,
            "uploadDate":     datetime.now(timezone.utc).isoformat(),
            "title":          file_name,
            "fileName":       file_name,
            "photographerId": photographer_id,
            "status":         "active",
        }
    )

    print(f"Metadata stored for photoId={photo_id}, tags={tags}")

    # ── 3. Return enriched event for Step Functions chaining ─────────────────
    return {**event, "tags": tags}
