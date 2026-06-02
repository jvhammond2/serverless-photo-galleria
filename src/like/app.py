"""
LikePhotoFunction — src/like/app.py
-------------------------------------
POST /like  (requires CustomerCognito authorizer)

Expected request body:
{
    "photoId": "uuid-string"
}

Atomically increments the `likes` counter on the PhotoMetadataTable item.
Returns the updated like count.
"""

import boto3
import json
import os

dynamodb = boto3.resource("dynamodb")

METADATA_TABLE = os.environ["METADATA_TABLE"]

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Content-Type": "application/json",
}


def handler(event, context):
    try:
        body = json.loads(event.get("body") or "{}")
        photo_id = body.get("photoId", "").strip()

        if not photo_id:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "photoId is required"}),
            }

        table = dynamodb.Table(METADATA_TABLE)

        response = table.update_item(
            Key={"photoId": photo_id},
            UpdateExpression="ADD likes :one",
            ExpressionAttributeValues={":one": 1},
            ConditionExpression="attribute_exists(photoId)",
            ReturnValues="UPDATED_NEW",
        )

        new_likes = int(response["Attributes"]["likes"])
        print(f"Like recorded: photoId={photo_id}, likes={new_likes}")

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({"photoId": photo_id, "likes": new_likes}),
        }

    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return {
            "statusCode": 404,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": f"Photo '{photo_id}' not found"}),
        }
    except Exception as e:
        print(f"Error processing like: {e}")
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Internal server error"}),
        }
