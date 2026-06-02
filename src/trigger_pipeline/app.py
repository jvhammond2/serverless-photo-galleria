"""
TriggerPipelineFunction — src/trigger_pipeline/app.py
------------------------------------------------------
POST /process
  Body: { "s3Key": "photo.jpg", "effects": ["bw","sharp"], "limitedEdition": false }

Why a Lambda wrapper instead of direct API → Step Functions integration?
  SAM's built-in StateMachine API event uses an AWS service integration that
  API Gateway controls entirely — it never adds CORS headers.  When a browser
  calls the endpoint, it gets a response with no Access-Control-Allow-Origin
  header and the fetch is blocked.  A Lambda proxy integration lets us add
  CORS headers ourselves, translate effect IDs → processing actions, and
  return a proper 200 immediately while the pipeline runs asynchronously.

Security: route uses PhotographerCognito authorizer (GalleriaUserPool only).
"""

import boto3
import json
import os
import uuid

sfn = boto3.client("stepfunctions")

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]
ORIGINALS_BUCKET  = os.environ["ORIGINALS_BUCKET"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

# Translate frontend effect card IDs to processing-Lambda action strings.
# An effect can map to zero, one, or multiple actions; duplicates are dropped.
EFFECT_TO_ACTIONS = {
    "hdr":        ["autoenhance"],
    "cinematic":  ["saturate"],
    "bw":         ["grayscale"],
    "portrait":   ["sharpen"],
    "landscape":  ["saturate"],
    "golden":     ["sepia"],
    "moody":      ["autoenhance"],
    "sharp":      ["sharpen"],
    "denoise":    ["denoise"],
    "vignette":   [],                # visual effect not yet in processing Lambda
    "film":       ["blur"],
    "colour_pop": ["saturate"],
    "vintage":    ["sepia"],
    "aerial":     ["autoenhance"],
}


def _photographer_id(event: dict) -> str:
    """Extract the photographer's Cognito sub from the authorizer claims."""
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email") or ""


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

    # Build ordered, deduplicated action list from selected effects
    effects = body.get("effects", [])
    seen, actions = set(), []
    for effect_id in effects:
        for action in EFFECT_TO_ACTIONS.get(effect_id, []):
            if action not in seen:
                seen.add(action)
                actions.append(action)

    execution_input = json.dumps({
        "s3Bucket":       ORIGINALS_BUCKET,
        "s3Key":          s3_key,
        "actions":        actions,
        "photographerId": _photographer_id(event),
    })

    try:
        resp = sfn.start_execution(
            stateMachineArn=STATE_MACHINE_ARN,
            name=f"galleria-{uuid.uuid4()}",
            input=execution_input,
        )
    except Exception as exc:
        return {"statusCode": 500, "headers": HEADERS,
                "body": json.dumps({"error": str(exc)})}

    return {
        "statusCode": 200,
        "headers": HEADERS,
        "body": json.dumps({
            "executionArn": resp["executionArn"],
            "message":      "Pipeline started — photo will appear in your library shortly",
        }),
    }
