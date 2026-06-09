"""
DownloadFunction — src/download/app.py
----------------------------------------
POST /get-download-url
Body: { "photoId": "...", "type": "preview" | "purchase" }

Two modes:

  preview   Short-lived (5 min) presigned GET URL for the full-size processed
            image in FullSizePreviewsS3Bucket.  Used by the modal "View Full
            Size" flow — the image is displayed inline in the browser.

  purchase  Longer-lived (15 min) presigned GET URL with a
            Content-Disposition: attachment header so the browser triggers a
            native download.  In a real checkout flow you would only call this
            after a successful payment; wire it behind your Stripe webhook.

Both bucket names come from environment variables so the template controls
which S3 bucket is used at each stage of the pipeline.

NOTE: The current template.yaml has DownloadFunction pointing at THUMBS_BUCKET.
That needs to change — see the fix list in the comment below.
"""

import base64
import boto3
import json
import os

from aws_xray_sdk.core import patch_all, xray_recorder
patch_all()

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

PREVIEWS_BUCKET = os.environ["PREVIEWS_BUCKET"]   # FullSizePreviewsS3Bucket
METADATA_TABLE  = os.environ["METADATA_TABLE"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

PREVIEW_TTL  = 5 * 60    #  5 minutes — inline viewing
PURCHASE_TTL = 15 * 60   # 15 minutes — download window after checkout


def _extract_user_id(event: dict) -> str | None:
    auth_header = (event.get("headers") or {}).get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        return None
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.b64decode(payload_b64))
        return claims.get("sub") or claims.get("email")
    except Exception:
        return None


def _error(status: int, message: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": message})}


def handler(event, context):
    user_id = _extract_user_id(event)
    if not user_id:
        return _error(401, "Missing or invalid Authorization token.")

    try:
        body     = json.loads(event.get("body") or "{}")
        photo_id = body["photoId"].strip()
        url_type = body.get("type", "preview").lower()   # "preview" or "purchase"
    except (KeyError, ValueError, json.JSONDecodeError):
        return _error(400, "Request body must contain 'photoId' and optionally 'type'.")

    if url_type not in ("preview", "purchase"):
        return _error(400, "'type' must be 'preview' or 'purchase'.")

    # ── Look up the photo record to get the S3 key ───────────────────────────
    table    = dynamodb.Table(METADATA_TABLE)
    response = table.get_item(Key={"photoId": photo_id})
    item     = response.get("Item")
    if not item:
        return _error(404, f"Photo '{photo_id}' not found in catalog.")

    preview_key = item.get("previewKey")
    if not preview_key:
        return _error(503, "This photo has not finished processing yet.")

    _annotate(photoId=photo_id, userId=user_id, urlType=url_type)

    # ── Generate the presigned URL ────────────────────────────────────────────
    if url_type == "preview":
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": PREVIEWS_BUCKET, "Key": preview_key},
            ExpiresIn=PREVIEW_TTL,
        )
        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({"url": url, "expiresIn": PREVIEW_TTL}),
        }

    # purchase — force download with a suggested filename
    filename = item.get("title", photo_id)
    url = s3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": PREVIEWS_BUCKET,
            "Key":    preview_key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
        },
        ExpiresIn=PURCHASE_TTL,
    )
    return {
        "statusCode": 200,
        "headers": HEADERS,
        "body": json.dumps({"url": url, "expiresIn": PURCHASE_TTL, "filename": filename}),
    }


def _annotate(**kwargs):
    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        if seg:
            for k, v in kwargs.items():
                seg.put_annotation(k, str(v))
    except Exception:
        pass


# ── Template fixes required for DownloadFunction ──────────────────────────────
#
#  BEFORE (broken — wrong bucket, missing metadata table):
#    Environment:
#      Variables:
#        THUMBS_BUCKET: !Ref ThumbsS3Bucket
#    Policies:
#      - S3ReadPolicy:
#          BucketName: !Sub ${AWS::StackName}-thumbs-${AWS::AccountId}
#
#  AFTER (correct):
#    Environment:
#      Variables:
#        PREVIEWS_BUCKET: !Ref FullSizePreviewsS3Bucket
#        METADATA_TABLE:  !Ref PhotoMetadataTable
#    Policies:
#      - S3ReadPolicy:
#          BucketName: !Sub ${AWS::StackName}-previews-${AWS::AccountId}
#      - DynamoDBReadPolicy:
#          TableName: !Ref PhotoMetadataTable
