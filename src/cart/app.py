"""
ShoppingCartFunction — src/cart/app.py
----------------------------------------
Handles all three cart operations on a single route:

  GET    /cart           → return the authenticated user's cart item list
  POST   /cart  { "photoId": "..." }  → add item
  DELETE /cart  { "photoId": "..." }  → remove item

User identity is read from the Cognito ID Token passed in the
Authorization header. The JWT payload is base64-decoded to extract
the `sub` claim (stable Cognito user UUID). This is safe because API
Gateway validates the token signature before the Lambda is invoked —
but only if you add a CognitoAuthorizer to GalleriaApi in template.yaml
(see the fix list comment at the bottom of this file).

DynamoDB schema (ShoppingCartTable, PK = userId):
  {
    "userId":     "cognito-sub-uuid",
    "cart_items": SS  (DynamoDB String Set of photoIds)
  }

Using a native DynamoDB StringSet means ADD / DELETE are atomic and
safe under concurrent requests from the same user.
"""

import base64
import boto3
import json
import os

dynamodb   = boto3.resource("dynamodb")
CART_TABLE = os.environ["CART_TABLE"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_user_id(event: dict) -> str | None:
    """
    Decode the Cognito ID Token from the Authorization header and return
    the `sub` claim.  Returns None if the token is missing or malformed.
    """
    auth_header = (event.get("headers") or {}).get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        return None
    try:
        # JWT = header.payload.signature — we only need the payload
        payload_b64 = token.split(".")[1]
        # Add padding so Python's base64 decoder is happy
        payload_b64 += "=" * (-len(payload_b64) % 4)
        claims = json.loads(base64.b64decode(payload_b64))
        return claims.get("sub") or claims.get("email")
    except Exception:
        return None


def _error(status: int, message: str) -> dict:
    return {
        "statusCode": status,
        "headers": HEADERS,
        "body": json.dumps({"error": message}),
    }


def _ok(data) -> dict:
    return {
        "statusCode": 200,
        "headers": HEADERS,
        "body": json.dumps(data),
    }


# ── Handler ───────────────────────────────────────────────────────────────────

def handler(event, context):
    user_id = _extract_user_id(event)
    if not user_id:
        return _error(401, "Missing or invalid Authorization token.")

    method = event.get("httpMethod", "GET").upper()
    table  = dynamodb.Table(CART_TABLE)

    # ── GET: return current cart ─────────────────────────────────────────────
    if method == "GET":
        response = table.get_item(Key={"userId": user_id})
        item     = response.get("Item", {})
        # DynamoDB returns StringSets as Python sets; convert to sorted list
        cart_items = sorted(item.get("cart_items", set()))
        return _ok({"userId": user_id, "items": cart_items})

    # ── POST / DELETE: parse body ────────────────────────────────────────────
    try:
        body     = json.loads(event.get("body") or "{}")
        photo_id = body["photoId"].strip()
        if not photo_id:
            raise ValueError
    except (KeyError, ValueError, json.JSONDecodeError):
        return _error(400, "Request body must contain a non-empty 'photoId'.")

    # ── POST: add item ───────────────────────────────────────────────────────
    if method == "POST":
        table.update_item(
            Key={"userId": user_id},
            # ADD is idempotent for StringSets — safe to call multiple times
            UpdateExpression="ADD cart_items :item",
            ExpressionAttributeValues={":item": {photo_id}},
        )
        return _ok({"message": f"'{photo_id}' added to cart."})

    # ── DELETE: remove item ──────────────────────────────────────────────────
    if method == "DELETE":
        table.update_item(
            Key={"userId": user_id},
            UpdateExpression="DELETE cart_items :item",
            ExpressionAttributeValues={":item": {photo_id}},
        )
        return _ok({"message": f"'{photo_id}' removed from cart."})

    return _error(405, f"HTTP method '{method}' is not allowed on this route.")


# ── Template fix required ─────────────────────────────────────────────────────
# Add a CognitoAuthorizer to GalleriaApi so API Gateway validates the JWT
# *before* this Lambda runs.  Example addition to template.yaml:
#
#   GalleriaApi:
#     Type: AWS::Serverless::Api
#     Properties:
#       ...
#       Auth:
#         DefaultAuthorizer: CognitoAuth
#         Authorizers:
#           CognitoAuth:
#             UserPoolArn: !GetAtt CustomerUserPool.Arn
#
# Then add  Auth: DefaultAuthorizer: NONE  on public routes (list-images, etc.)
# so only the cart endpoint requires authentication.
