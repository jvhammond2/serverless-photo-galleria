"""
UnlikePhotoFunction — src/unlike/app.py
-----------------------------------------
POST /unlike   { "photoId": "..." }

Removes the authenticated user from the photo's `likedBy` StringSet and
decrements the `likes` counter.  This is the symmetric inverse of the
existing LikePhotoFunction.

Idempotent: unliking a photo you never liked (or already unliked) returns
200 with a no-op message rather than an error.

DynamoDB schema assumed on PhotoMetadataTable:
  - photoId   (String, PK)
  - likes     (Number)  — denormalised count, kept in sync with likedBy
  - likedBy   (StringSet) — set of userId strings
"""

import boto3
import json
import os
from boto3.dynamodb.conditions import Attr

dynamodb = boto3.resource("dynamodb")

METADATA_TABLE = os.environ["METADATA_TABLE"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


def _user_id(event: dict) -> str | None:
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email")


def _error(status: int, msg: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


def handler(event, context):
    user_id = _user_id(event)
    if not user_id:
        return _error(401, "Missing or invalid Authorization token.")

    try:
        body     = json.loads(event.get("body") or "{}")
        photo_id = body["photoId"].strip()
    except (KeyError, ValueError, json.JSONDecodeError):
        return _error(400, "Request body must contain 'photoId'.")

    table = dynamodb.Table(METADATA_TABLE)

    # Verify photo exists
    resp = table.get_item(
        Key={"photoId": photo_id},
        ProjectionExpression="photoId, likes, likedBy",
    )
    item = resp.get("Item")
    if not item:
        return _error(404, f"Photo '{photo_id}' not found.")

    liked_by = item.get("likedBy") or set()
    if user_id not in liked_by:
        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({
                "message": "You haven't liked this photo.",
                "likes": int(item.get("likes", 0)),
            }),
        }

    # Remove user from likedBy set and decrement counter atomically.
    # REMOVE on a StringSet element + ADD -1 to likes in a single request.
    resp = table.update_item(
        Key={"photoId": photo_id},
        UpdateExpression="DELETE likedBy :uid ADD likes :neg",
        ConditionExpression=Attr("likedBy").contains(user_id),
        ExpressionAttributeValues={
            ":uid": {user_id},   # DynamoDB StringSet
            ":neg": -1,
        },
        ReturnValues="UPDATED_NEW",
    )

    new_likes = int(resp.get("Attributes", {}).get("likes", 0))

    return {
        "statusCode": 200,
        "headers": HEADERS,
        "body": json.dumps({
            "message": "Like removed.",
            "likes": new_likes,
        }),
    }
