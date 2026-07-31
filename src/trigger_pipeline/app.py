"""
TriggerPipelineFunction -- src/trigger_pipeline/app.py
------------------------------------------------------
POST /process
  Body: {
    "s3Key": "photo.jpg",
    "adjustments": [{"id":"exposure","value":25}, {"id":"contrast","value":-10}],
    "limitedEdition": false,
    "category": "landscape"
  }

AWS Cert Note (DVA-C02): The adjustments list is passed through to Step
Functions as-is. Processing decisions live in ProcessingFunction, keeping
this Lambda thin (single-responsibility principle).

Security: route uses PhotographerCognito authorizer (GalleriaUserPool only).
"""

import boto3
import json
import os
import uuid

from aws_xray_sdk.core import patch_all, xray_recorder
patch_all()

sfn = boto3.client("stepfunctions")

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ORIGINALS_BUCKET  = os.environ["ORIGINALS_BUCKET"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

VALID_CATEGORIES = {
    "abstract", "aerial", "animals", "bw", "boudoir", "celebrities",
    "city", "commercial", "concert", "family", "fashion", "film",
    "fineart", "food", "journalism", "landscape", "macro", "nature",
    "night", "other", "people", "performing", "sport", "stilllife",
    "street", "transportation", "travel", "underwater", "urbex", "wedding",
}

VALID_ADJ_IDS = {
    "exposure", "brilliance", "highlights", "shadows", "brightness",
    "contrast", "blackpoint", "saturation", "vibrance", "warmth", "tint",
    "sharpness", "definition", "noiseReduction", "vignette",
}


def _photographer_id(event):
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email") or ""


def _sanitize_adjustments(raw):
    """Validate and clamp each adjustment entry."""
    result = []
    seen   = set()
    for item in (raw or []):
        if not isinstance(item, dict):
            continue
        adj_id = str(item.get("id", "")).strip()
        if adj_id not in VALID_ADJ_IDS or adj_id in seen:
            continue
        try:
            value = int(item["value"])
        except (KeyError, TypeError, ValueError):
            continue
        value = max(-100, min(100, value))
        if value != 0:
            result.append({"id": adj_id, "value": value})
        seen.add(adj_id)
    return result


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return {"statusCode": 400, "headers": HEADERS,
                "body": json.dumps({"error": "Invalid JSON body"})}

    s3_key = (body.get("s3Key") or "").strip()
    if not s3_key:
        return {"statusCode": 400, "headers": HEADERS,
                "body": json.dumps({"error": "s3Key is required"})}

    category = (body.get("category") or "other").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "other"

    VALID_MOODS = {"warm","cool","green","purple","neutral","dark","light",""}
    color_mood  = (body.get("colorMood") or "").strip().lower()
    if color_mood not in VALID_MOODS:
        color_mood = ""

    adjustments     = _sanitize_adjustments(body.get("adjustments", []))
    photographer_id = _photographer_id(event)

    execution_input = json.dumps({
        "s3Bucket":       ORIGINALS_BUCKET,
        "s3Key":          s3_key,
        "adjustments":    adjustments,
        "photographerId": photographer_id,
        "category":       category,
        "colorMood":      color_mood,
    })

    _annotate(photographerId=photographer_id, category=category,
              s3Key=s3_key, adjustmentCount=len(adjustments))

    try:
        resp = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=str(uuid.uuid4()),
            input=execution_input,
        )
        execution_arn = resp["executionArn"]
        print(f"[pipeline] Started: {execution_arn}  key={s3_key}  adj={adjustments}")
        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({"executionArn": execution_arn, "s3Key": s3_key}),
        }
    except Exception as e:
        print(f"[pipeline] Step Functions error: {e}")
        return {
            "statusCode": 500,
            "headers": HEADERS,
            "body": json.dumps({"error": "Failed to start pipeline"}),
        }


def _annotate(**kwargs):
    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        if seg:
            for k, v in kwargs.items():
                seg.put_annotation(k, str(v))
    except Exception:
        pass
