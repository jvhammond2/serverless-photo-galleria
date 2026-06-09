"""
CompositionFeedbackFunction — src/composition_feedback/app.py
--------------------------------------------------------------
GET /composition-feedback?photoId=<id>

Returns AI-generated composition critique for a photo by calling
Claude Haiku via Amazon Bedrock.  Result is cached in the photo's
DynamoDB metadata item so Bedrock is only called once per photo.

Design (cost-conscious):
  - On-demand only; never auto-triggered during the upload pipeline.
  - Uses claude-3-haiku (lowest cost Claude vision model).
  - Thumbnail (~200-400 KB) is used, not the full original.
  - Cached result returned on every subsequent request at no model cost.

AWS Cert Note (MLA-C01 / DVA-C02):
  Bedrock InvokeModel is a synchronous API call -- the Lambda waits
  for the model response (typically 2-5 s for Haiku on a small image).
  The IAM principal needs bedrock:InvokeModel scoped to the specific
  model ARN to follow least-privilege.
"""

import base64
import json
import os

import boto3

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,OPTIONS",
}

# Bedrock model — Haiku is fastest/cheapest Claude vision model
MODEL_ID = "anthropic.claude-3-haiku-20240307-v1:0"

SYSTEM_PROMPT = (
    "You are an experienced fine-art photography critic and educator. "
    "Your role is to give photographers clear, specific, and encouraging "
    "feedback on their composition. Be concise — your entire response must "
    "be valid JSON matching the schema described below."
)

USER_PROMPT_TEMPLATE = """Analyze the composition of this photograph.

AI-detected tags: {tags}
Category: {category}

Return ONLY a JSON object — no markdown, no prose outside the JSON — matching this schema:
{{
  "score": <integer 1-10>,
  "summary": "<one sentence overall impression, max 20 words>",
  "strengths": ["<strength 1, max 12 words>", "<strength 2, max 12 words>"],
  "improvements": ["<tip 1, max 15 words>", "<tip 2, max 15 words>"],
  "elements": {{
    "rule_of_thirds": "<brief assessment, max 12 words>",
    "light": "<brief assessment, max 12 words>",
    "depth": "<brief assessment, max 12 words>",
    "balance": "<brief assessment, max 12 words>"
  }}
}}"""


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    params     = event.get("queryStringParameters") or {}
    photo_id   = params.get("photoId", "").strip()

    if not photo_id:
        return _err(400, "photoId is required")

    # ── 1. Auth check ─────────────────────────────────────────────────────────
    auth = (event.get("headers") or {}).get("Authorization", "")
    if not auth:
        return _err(401, "Authorization header required")

    # ── 2. Load photo metadata ────────────────────────────────────────────────
    ddb   = boto3.resource("dynamodb")
    table = ddb.Table(os.environ["METADATA_TABLE"])

    resp  = table.get_item(Key={"photoId": photo_id})
    item  = resp.get("Item")
    if not item:
        return _err(404, f"Photo {photo_id} not found")

    # ── 3. Return cached feedback if available ────────────────────────────────
    cached = item.get("compositionFeedback")
    if cached:
        return _ok({"feedback": cached, "cached": True})

    # ── 4. Fetch thumbnail from S3 ────────────────────────────────────────────
    thumb_key = item.get("thumbnailKey", "")
    if not thumb_key:
        return _err(422, "No thumbnail available yet — photo may still be processing")

    s3 = boto3.client("s3")
    try:
        obj       = s3.get_object(Bucket=os.environ["THUMBS_BUCKET"], Key=thumb_key)
        img_bytes = obj["Body"].read()
        img_b64   = base64.standard_b64encode(img_bytes).decode()
        # Detect media type from key extension
        ext       = thumb_key.rsplit(".", 1)[-1].lower()
        media_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                     "png": "image/png",  "webp": "image/webp"}
        media_type = media_map.get(ext, "image/jpeg")
    except Exception as e:
        return _err(500, f"Failed to fetch thumbnail: {e}")

    # ── 5. Build tags / category strings for prompt ───────────────────────────
    raw_tags  = item.get("tags", [])
    tag_names = [t.get("Name", t) if isinstance(t, dict) else str(t)
                 for t in raw_tags[:15]]  # cap at 15 to keep prompt small
    tags_str  = ", ".join(tag_names) if tag_names else "none detected"
    category  = item.get("category", "unspecified")

    # ── 6. Call Bedrock Haiku ─────────────────────────────────────────────────
    bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": media_type,
                            "data":       img_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": USER_PROMPT_TEMPLATE.format(
                            tags=tags_str, category=category
                        ),
                    },
                ],
            }
        ],
    }

    try:
        br_resp  = bedrock.invoke_model(
            modelId     = MODEL_ID,
            contentType = "application/json",
            accept      = "application/json",
            body        = json.dumps(body),
        )
        raw_text = json.loads(br_resp["body"].read())["content"][0]["text"].strip()
    except Exception as e:
        return _err(502, f"Bedrock call failed: {e}")

    # ── 7. Parse and validate JSON response ───────────────────────────────────
    # Haiku may occasionally wrap JSON in a markdown code block; strip it.
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        feedback = json.loads(raw_text)
    except json.JSONDecodeError:
        # Return the raw text so the UI can still display something
        feedback = {"summary": raw_text, "score": None,
                    "strengths": [], "improvements": [], "elements": {}}

    # ── 8. Cache in DynamoDB ──────────────────────────────────────────────────
    try:
        table.update_item(
            Key={"photoId": photo_id},
            UpdateExpression="SET compositionFeedback = :f",
            ExpressionAttributeValues={":f": feedback},
        )
    except Exception as e:
        # Non-fatal — return result even if cache write fails
        print(f"[composition] Cache write failed (non-fatal): {e}")

    return _ok({"feedback": feedback, "cached": False})


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
