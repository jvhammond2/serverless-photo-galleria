"""
RefundFunction — src/refund/app.py
------------------------------------
POST /refund   { "sessionId": "cs_live_...", "reason": "duplicate" }

Initiates a full Stripe refund for a completed order and marks the order
as refunded in the Orders table so the download link stops working.

Flow:
  1. Look up the order in OrdersTable by sessionId.
  2. Verify the requesting user owns this order (their userId matches).
  3. Check the order hasn't already been refunded.
  4. Call Stripe's Refund API using the stored paymentIntentId.
  5. Update the order record: status → "refunded", refundedAt → now.
  6. Return the Stripe refund ID to the caller.

Refund reasons accepted by Stripe:
  "duplicate" | "fraudulent" | "requested_by_customer"

Access control:
  - Customers can refund their own orders (within Stripe's refund window,
    typically 90 days from charge).
  - Photographers cannot initiate refunds through this endpoint — they
    manage disputes through the Stripe dashboard.

Prerequisites:
  - OrdersTable items must have: sessionId (PK), userId, paymentIntentId,
    status, amount.
  - SSM parameter /serverless-photo-galleria/stripe-secret-key must exist.
"""

import boto3
import json
import os
import urllib.request
import urllib.error
import urllib.parse
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
ssm      = boto3.client("ssm")

ORDERS_TABLE            = os.environ["ORDERS_TABLE"]
METADATA_TABLE          = os.environ["METADATA_TABLE"]
STRIPE_SECRET_KEY_PARAM = os.environ["STRIPE_SECRET_KEY_PARAM"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

VALID_REASONS = {"duplicate", "fraudulent", "requested_by_customer"}


def _user_id(event: dict) -> str | None:
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email")


def _get_stripe_key() -> str:
    resp = ssm.get_parameter(Name=STRIPE_SECRET_KEY_PARAM, WithDecryption=True)
    return resp["Parameter"]["Value"]


def _stripe_refund(payment_intent_id: str, reason: str, stripe_key: str) -> dict:
    """Call Stripe Refunds API via urllib (no stripe SDK needed in Lambda)."""
    payload = urllib.parse.urlencode({
        "payment_intent": payment_intent_id,
        "reason":         reason,
    }).encode()

    req = urllib.request.Request(
        "https://api.stripe.com/v1/refunds",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {stripe_key}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read())
        raise RuntimeError(body.get("error", {}).get("message", str(e)))


def _error(status: int, msg: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


def handler(event, context):
    user_id = _user_id(event)
    if not user_id:
        return _error(401, "Missing or invalid Authorization token.")

    try:
        body       = json.loads(event.get("body") or "{}")
        session_id = body["sessionId"].strip()
        reason     = body.get("reason", "requested_by_customer").lower()
    except (KeyError, ValueError, json.JSONDecodeError):
        return _error(400, "Request body must contain 'sessionId'.")

    if reason not in VALID_REASONS:
        return _error(400, f"'reason' must be one of: {', '.join(sorted(VALID_REASONS))}.")

    # ── 1. Look up the order ──────────────────────────────────────────────
    orders_table = dynamodb.Table(ORDERS_TABLE)
    resp  = orders_table.get_item(Key={"sessionId": session_id})
    order = resp.get("Item")

    if not order:
        return _error(404, f"Order '{session_id}' not found.")

    # ── 2. Ownership check ────────────────────────────────────────────────
    if order.get("userId") != user_id:
        # Return 404 rather than 403 to avoid leaking order existence to
        # other users (OWASP BOLA / IDOR prevention)
        return _error(404, f"Order '{session_id}' not found.")

    # ── 3. Already refunded? ──────────────────────────────────────────────
    if order.get("status") == "refunded":
        return _error(409, "This order has already been refunded.")

    if order.get("status") != "completed":
        return _error(409, f"Cannot refund an order with status '{order.get('status')}'.")

    payment_intent_id = order.get("paymentIntentId")
    if not payment_intent_id:
        return _error(500, "Order is missing payment reference. Contact support.")

    # ── 4. Issue Stripe refund ────────────────────────────────────────────
    try:
        stripe_key    = _get_stripe_key()
        refund_result = _stripe_refund(payment_intent_id, reason, stripe_key)
    except RuntimeError as e:
        return _error(502, f"Stripe refund failed: {e}")
    except Exception as e:
        return _error(500, f"Unexpected error issuing refund: {e}")

    # ── 5. Mark order as refunded ─────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    orders_table.update_item(
        Key={"sessionId": session_id},
        UpdateExpression=(
            "SET #status = :refunded, "
            "refundedAt = :now, "
            "stripeRefundId = :rid, "
            "refundReason = :reason"
        ),
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":refunded": "refunded",
            ":now":      now,
            ":rid":      refund_result.get("id", ""),
            ":reason":   reason,
        },
    )

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps({
            "message":      "Refund issued successfully.",
            "refundId":     refund_result.get("id"),
            "status":       refund_result.get("status"),
            "amount":       refund_result.get("amount"),   # cents
            "refundedAt":   now,
        }),
    }
