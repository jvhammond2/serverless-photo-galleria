"""
ModerationFunction — Step 1 of the pipeline.
Calls Rekognition DetectModerationLabels on the original S3 image.
If any label confidence > 80%, quarantines the photo in DynamoDB and
signals the state machine to stop (moderated=True).
Clean photos pass through unchanged (moderated=False).
"""
import json
import os
import uuid
from datetime import datetime, timezone

import boto3
from aws_xray_sdk.core import patch_all, xray_recorder
patch_all()

CONFIDENCE_THRESHOLD = 80.0

rek  = boto3.client("rekognition")
ddb  = boto3.resource("dynamodb")


def handler(event, context):
    s3_bucket       = event["s3Bucket"]
    s3_key          = event["s3Key"]
    adjustments         = event.get("adjustments", [])
    photographer_id = event.get("photographerId", "")
    category        = event.get("category", "other")
    _annotate(s3Key=s3_key, photographerId=photographer_id, category=category)

    try:
        resp   = rek.detect_moderation_labels(
            Image={"S3Object": {"Bucket": s3_bucket, "Name": s3_key}},
            MinConfidence=CONFIDENCE_THRESHOLD,
        )
        labels = resp.get("ModerationLabels", [])
    except Exception as e:
        # On Rekognition error, let the photo through (fail-open) to avoid
        # blocking legitimate uploads due to transient AWS issues.
        print(f"Moderation check error (fail-open): {e}")
        return _pass_through(s3_bucket, s3_key, adjustments, photographer_id, category)

    if not labels:
        # Clean — no moderation labels detected
        return _pass_through(s3_bucket, s3_key, adjustments, photographer_id, category)

    # Flagged — record in DynamoDB and stop the pipeline
    flagged_labels = [
        {"name": lbl["Name"], "confidence": round(lbl["Confidence"], 1)}
        for lbl in labels
    ]
    photo_id   = str(uuid.uuid4())
    file_name  = s3_key.split("/")[-1]
    now_iso    = datetime.now(timezone.utc).isoformat()

    table = ddb.Table(os.environ["METADATA_TABLE"])
    table.put_item(Item={
        "photoId":          photo_id,
        "fileName":         file_name,
        "originalKey":      s3_key,
        "thumbnailKey":     "",
        "previewKey":       "",
        "uploadDate":       now_iso,
        "uploadedAt":       now_iso,      # GSI sort key for category-uploadedAt-index
        "category":         category,     # GSI partition key
        "likes":            0,
        "tags":             [],
        "moderationStatus": "flagged",
        "moderationLabels": json.dumps(flagged_labels),
        "title":            file_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").title(),
    })

    print(f"Photo {photo_id} QUARANTINED — labels: {flagged_labels}")
    _annotate(photoId=photo_id, moderated="true")
    return {
        "s3Bucket":       s3_bucket,
        "s3Key":          s3_key,
        "adjustments":        adjustments,
        "moderated":      True,
        "photoId":        photo_id,
        "photographerId": photographer_id,
        "category":       category,
    }


def _pass_through(s3_bucket, s3_key, adjustments, photographer_id="", category="other"):
    _annotate(moderated="false")
    return {
        "s3Bucket":       s3_bucket,
        "s3Key":          s3_key,
        "adjustments":        adjustments,
        "moderated":      False,
        "photographerId": photographer_id,
        "category":       category,
    }


def _annotate(**kwargs):
    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        if seg:
            for k, v in kwargs.items():
                seg.put_annotation(k, str(v))
    except Exception:
        pass
