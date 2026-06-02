"""
EmbedPhotoFunction — Step 6 of the pipeline (after BedrockEnrich).
Combines the AI description + Rekognition tags into a text representation,
calls Amazon Titan Embed Text V2 (256 dimensions) via Bedrock,
and stores the embedding in DynamoDB for semantic search.
"""
import json
import os
import math

import boto3
from decimal import Decimal

bedrock = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))
ddb     = boto3.resource("dynamodb")

EMBED_MODEL    = "amazon.titan-embed-text-v2:0"
EMBED_DIMS     = 256  # 256 | 512 | 1024 — 256 keeps DDB item small (~2 KB)


def handler(event, context):
    photo_id = event["photoId"]
    table    = ddb.Table(os.environ["METADATA_TABLE"])

    resp = table.get_item(Key={"photoId": photo_id})
    item = resp.get("Item", {})

    ai_description = item.get("aiDescription", "")
    ai_title       = item.get("aiTitle", "")
    tags           = item.get("tags", [])
    file_name      = item.get("fileName", "")

    # Build a rich text representation to embed
    tags_str  = " ".join(tags[:20])
    embed_text = f"{ai_title}. {ai_description} {tags_str} {file_name}".strip()
    if not embed_text:
        print(f"No text to embed for {photo_id}, skipping")
        return {"photoId": photo_id}

    try:
        response = bedrock.invoke_model(
            modelId=EMBED_MODEL,
            body=json.dumps({
                "inputText":            embed_text,
                "dimensions":           EMBED_DIMS,
                "normalize":            True,
            }),
            contentType="application/json",
            accept="application/json",
        )
        raw_body = json.loads(response["body"].read())
        embedding = raw_body["embedding"]  # list of floats

        # DynamoDB stores numbers as Decimal
        embedding_dec = [Decimal(str(round(v, 6))) for v in embedding]

        table.update_item(
            Key={"photoId": photo_id},
            UpdateExpression="SET embedding = :e",
            ExpressionAttributeValues={":e": embedding_dec},
        )
        print(f"Embedded {photo_id}: {len(embedding)} dims")

    except Exception as e:
        print(f"Embedding error for {photo_id}: {e} — skipping, search falls back to tags")

    return {"photoId": photo_id}
