"""
FollowFunction — src/follow/app.py
------------------------------------
Three routes, one Lambda:

  POST   /follow              body: {"photographerId": "abc123"}
                              → Follow a photographer (PutItem in FollowsTable)

  DELETE /follow/{followeeId} → Unfollow a photographer (DeleteItem)

  GET    /follow              → List all photographer IDs the caller follows
                                Returns: {"following": ["id1", "id2", ...]}

The caller's identity comes from the CustomerCognito JWT — the Cognito sub is
used as followerId so customers can only manage their own follow list.

AWS Cert Note (SAA-C03 / DVA-C02):
  FollowsTable uses a composite key: followerId (HASH) + followeeId (RANGE).
  - PutItem / DeleteItem are O(1) point operations — no scan needed.
  - Query by followerId returns all follows for one customer in a single call.
  This is the textbook "adjacency list" pattern for storing relationships in
  DynamoDB without a join table scan.
"""

import boto3
import json
import os

from aws_xray_sdk.core import patch_all, xray_recorder
patch_all()

dynamodb = boto3.resource("dynamodb")

FOLLOWS_TABLE = os.environ["FOLLOWS_TABLE"]

HEADERS = {
    "Content-Type":                "application/json",
    "Access-Control-Allow-Origin": "*",
}


def _caller_id(event: dict) -> str:
    """Extract the customer's Cognito sub from the authorizer claims."""
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email") or ""


def _error(status: int, msg: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


def handler(event, context):
    method = event.get("httpMethod", "")
    table  = dynamodb.Table(FOLLOWS_TABLE)
    caller = _caller_id(event)

    if not caller:
        return _error(401, "Unauthorized")
    _annotate(followerId=caller, action=method)

    # ── POST /follow ──────────────────────────────────────────────────────────
    if method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return _error(400, "Invalid JSON body")

        followee_id = (body.get("photographerId") or "").strip()
        if not followee_id:
            return _error(400, "photographerId is required")
        if followee_id == caller:
            return _error(400, "Cannot follow yourself")

        table.put_item(Item={"followerId": caller, "followeeId": followee_id})

        return {
            "statusCode": 200,
            "headers":    HEADERS,
            "body":       json.dumps({"followed": followee_id}),
        }

    # ── DELETE /follow/{followeeId} ───────────────────────────────────────────
    if method == "DELETE":
        path_params = event.get("pathParameters") or {}
        followee_id = (path_params.get("followeeId") or "").strip()
        if not followee_id:
            return _error(400, "followeeId path parameter is required")

        table.delete_item(Key={"followerId": caller, "followeeId": followee_id})

        return {
            "statusCode": 200,
            "headers":    HEADERS,
            "body":       json.dumps({"unfollowed": followee_id}),
        }

    # ── GET /follow ───────────────────────────────────────────────────────────
    if method == "GET":
        resp = table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("followerId").eq(caller),
            ProjectionExpression="followeeId",
        )
        ids = [item["followeeId"] for item in resp.get("Items", [])]

        return {
            "statusCode": 200,
            "headers":    HEADERS,
            "body":       json.dumps({"following": ids}),
        }

    return _error(405, "Method not allowed")


def _annotate(**kwargs):
    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        if seg:
            for k, v in kwargs.items():
                seg.put_annotation(k, str(v))
    except Exception:
        pass
