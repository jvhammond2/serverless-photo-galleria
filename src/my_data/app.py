"""
MyDataFunction — src/my_data/app.py
--------------------------------------
GET /my-data

GDPR Article 20 — Right to Data Portability.

Returns a complete, machine-readable JSON export of everything the platform
holds about the authenticated user, including:

  - profile information (from the JWT claims — we don't store a separate
    profile table for customers, only photographers)
  - consent records
  - shopping cart
  - order history (purchase records)
  - liked photos (stored as metadata on PhotoMetadataTable items)

The response sets Content-Disposition: attachment so the browser downloads
the file rather than displaying it inline.

Privacy note: this endpoint writes an audit record on every call so you have
a log of who exported their data and when.
"""

import boto3
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")

CONSENT_TABLE  = os.environ["CONSENT_TABLE"]
CART_TABLE     = os.environ["CART_TABLE"]
ORDERS_TABLE   = os.environ["ORDERS_TABLE"]
METADATA_TABLE = os.environ["METADATA_TABLE"]
AUDIT_LOG_TABLE = os.environ["AUDIT_LOG_TABLE"]

HEADERS = {
    "Content-Type": "application/json",
    "Content-Disposition": 'attachment; filename="my-galleria-data.json"',
    "Access-Control-Allow-Origin": "*",
}

SEVEN_YEARS = 7 * 365 * 24 * 3600


class DecimalEncoder(json.JSONEncoder):
    """DynamoDB returns Decimal for numbers — convert to float for JSON."""
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _user_id(event: dict) -> tuple[str | None, str | None]:
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub"), claims.get("email")


def _write_audit(user_id: str, action: str, detail: str):
    dynamodb.Table(AUDIT_LOG_TABLE).put_item(Item={
        "auditId":   str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "userId":    user_id,
        "action":    action,
        "detail":    detail,
        "expiresAt": int(time.time()) + SEVEN_YEARS,
    })


def _error(status: int, msg: str) -> dict:
    return {
        "statusCode": status,
        "headers":    {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body":       json.dumps({"error": msg}),
    }


def handler(event, context):
    user_sub, user_email = _user_id(event)
    if not user_sub:
        return _error(401, "Missing or invalid Authorization token.")

    export = {
        "exportedAt":   datetime.now(timezone.utc).isoformat(),
        "userId":       user_sub,
        "email":        user_email,
        "dataCategories": {},
    }

    # ── Consent records ───────────────────────────────────────────────────────
    try:
        resp = dynamodb.Table(CONSENT_TABLE).query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq(user_sub)
        )
        export["dataCategories"]["consentHistory"] = resp.get("Items", [])
    except Exception as e:
        export["dataCategories"]["consentHistory"] = {"error": str(e)}

    # ── Shopping cart ─────────────────────────────────────────────────────────
    try:
        resp = dynamodb.Table(CART_TABLE).get_item(Key={"userId": user_sub})
        export["dataCategories"]["shoppingCart"] = resp.get("Item", {})
    except Exception as e:
        export["dataCategories"]["shoppingCart"] = {"error": str(e)}

    # ── Order history ─────────────────────────────────────────────────────────
    try:
        resp = dynamodb.Table(ORDERS_TABLE).scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("userId").eq(user_sub)
        )
        export["dataCategories"]["orders"] = resp.get("Items", [])
    except Exception as e:
        export["dataCategories"]["orders"] = {"error": str(e)}

    # ── Liked photos ──────────────────────────────────────────────────────────
    # PhotoMetadataTable stores a 'likedBy' StringSet; scan for items where
    # the user appears in that set.
    try:
        resp = dynamodb.Table(METADATA_TABLE).scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("likedBy").contains(user_sub),
            ProjectionExpression="photoId, title, #ts",
            ExpressionAttributeNames={"#ts": "timestamp"},
        )
        export["dataCategories"]["likedPhotos"] = resp.get("Items", [])
    except Exception as e:
        export["dataCategories"]["likedPhotos"] = {"error": str(e)}

    # ── Audit this export ─────────────────────────────────────────────────────
    _write_audit(user_sub, "data:exported", "GDPR Art.20 portability export")

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps(export, cls=DecimalEncoder, indent=2),
    }
