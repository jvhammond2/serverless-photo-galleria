"""
BedrockEnrichFunction — Step 5 of the pipeline (after TagImage).
Reads Rekognition tags + fileName from DynamoDB, then calls Claude 3 Haiku
via Amazon Bedrock to generate:
  - aiTitle        : a compelling fine-art print title
  - aiDescription  : 2-3 sentence description for the customer gallery
  - suggestedPrice : recommended USD price (integer cents)
Stores all three back to DynamoDB.
"""
import json
import os
import re

import boto3
from boto3.dynamodb.conditions import Attr

BEDROCK_MODEL = "anthropic.claude-3-haiku-20240307-v1:0"

bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
ddb     = boto3.resource("dynamodb")


def handler(event, context):
    photo_id = event["photoId"]
    table    = ddb.Table(os.environ["METADATA_TABLE"])

    # Fetch current photo metadata (tags written by TaggingFunction)
    resp = table.get_item(Key={"photoId": photo_id})
    item = resp.get("Item", {})

    file_name = item.get("fileName", "photo.jpg")
    tags      = item.get("tags", [])
    base_name = file_name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")

    # ── Build the Bedrock prompt ──────────────────────────────────────────
    tags_str = ", ".join(tags[:20]) if tags else "landscape photograph"
    prompt   = f"""You are a fine-art photography curator writing compelling copy for an online print shop.

A photographer has uploaded an image with the filename "{base_name}" and AI-detected subjects: {tags_str}.

Respond with ONLY a JSON object (no markdown, no explanation) in this exact format:
{{
  "title": "A concise, evocative fine-art print title (4-8 words, titlecase, no quotes)",
  "description": "Two to three sentences of compelling gallery copy that helps a buyer imagine this print on their wall. Mention mood, light, and subject. End with a period.",
  "suggestedPriceCents": 2999
}}

For suggestedPriceCents, choose from: 999 (small everyday scene), 1999 (striking composition), 2999 (strong artistic merit), 3999 (exceptional or rare subject), 4999 (museum-quality or iconic scene).
"""

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 300,
        "temperature": 0.7,
        "messages": [{"role": "user", "content": prompt}],
    })

    try:
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL,
            body=body,
            contentType="application/json",
            accept="application/json",
        )
        raw = json.loads(response["body"].read())
        text = raw["content"][0]["text"].strip()

        # Strip any accidental markdown fences
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        ai_data = json.loads(text)
        ai_title       = str(ai_data.get("title", base_name))[:200]
        ai_description = str(ai_data.get("description", ""))[:1000]
        suggested_price = int(ai_data.get("suggestedPriceCents", 2999))

    except Exception as e:
        print(f"Bedrock enrichment error for {photo_id}: {e}")
        # Graceful fallback — don't fail the pipeline
        ai_title        = base_name.title()
        ai_description  = f"A stunning {tags_str.split(',')[0].strip().lower()} photograph available as a fine-art print."
        suggested_price = 2999

    # ── Write back to DynamoDB ────────────────────────────────────────────
    table.update_item(
        Key={"photoId": photo_id},
        UpdateExpression="SET aiTitle = :t, aiDescription = :d, suggestedPriceCents = :p, moderationStatus = if_not_exists(moderationStatus, :clean)",
        ExpressionAttributeValues={
            ":t":     ai_title,
            ":d":     ai_description,
            ":p":     suggested_price,
            ":clean": "clean",
        },
    )

    print(f"Enriched {photo_id}: title='{ai_title}', price={suggested_price}")
    return {"photoId": photo_id, "aiTitle": ai_title}
