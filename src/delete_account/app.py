"""
DeleteAccountFunction — src/delete_account/app.py
---------------------------------------------------
POST /delete-account   { "confirm": true }

GDPR Article 17 — Right to Erasure ("right to be forgotten").

What this does, in order:
  1. Verifies the user sent { "confirm": true } — an intentional double-check
     so a mis-tap doesn't destroy an account.
  2. Deletes the user's Cognito account.
  3. Deletes the user's shopping cart row.
  4. Deletes the user's consent records.
  5. Anonymises the user's orders (replaces userId/email with a tombstone string
     so financial records remain intact for accounting purposes per GDPR Art. 17(3)(b)).
  6. Writes an audit record confirming deletion.

NOTE: Photos the user purchased are deliberately NOT deleted.  The sale is a
financial transaction; the order record must be kept (typically 6–10 years
depending on jurisdiction) but personal identifiers are scrubbed.

IMPORTANT: This function only deletes the CustomerUserPool account.  If the
same email also has a PhotographerPool account, that requires a separate flow
(photographer data deletion is more complex — their uploaded photos affect other
users' galleries).
"""

import boto3
import json
import os
import time
import uuid
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
cognito  = boto3.client("cognito-idp")

CONSENT_TABLE    = os.environ["CONSENT_TABLE"]
CART_TABLE       = os.environ["CART_TABLE"]
ORDERS_TABLE     = os.environ["ORDERS_TABLE"]
AUDIT_LOG_TABLE  = os.environ["AUDIT_LOG_TABLE"]
CUSTOMER_POOL_ID = os.environ["CUSTOMER_POOL_ID"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

SEVEN_YEARS = 7 * 365 * 24 * 3600
TOMBSTONE   = "[DELETED]"


def _user_id(event: dict) -> tuple[str | None, str | None]:
    """Return (sub, email) from Cognito authorizer claims."""
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
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


def handler(event, context):
    user_sub, user_email = _user_id(event)
    if not user_sub:
        return _error(401, "Missing or invalid Authorization token.")

    # ── 1. Require explicit confirmation ──────────────────────────────────────
    try:
        body    = json.loads(event.get("body") or "{}")
        confirm = body.get("confirm", False)
    except (json.JSONDecodeError, ValueError):
        return _error(400, "Invalid JSON body.")

    if confirm is not True:
        return _error(400, "Request body must include { \"confirm\": true } to proceed.")

    errors = []

    # ── 2. Delete Cognito account ─────────────────────────────────────────────
    try:
        cognito.admin_delete_user(
            UserPoolId=CUSTOMER_POOL_ID,
            Username=user_sub,
        )
    except cognito.exceptions.UserNotFoundException:
        pass   # already gone — idempotent
    except Exception as e:
        errors.append(f"cognito: {e}")

    # ── 3. Delete shopping cart ───────────────────────────────────────────────
    try:
        dynamodb.Table(CART_TABLE).delete_item(Key={"userId": user_sub})
    except Exception as e:
        errors.append(f"cart: {e}")

    # ── 4. Delete consent records ─────────────────────────────────────────────
    try:
        consent_table = dynamodb.Table(CONSENT_TABLE)
        resp  = consent_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq(user_sub)
        )
        with consent_table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={
                    "userId":         item["userId"],
                    "consentVersion": item["consentVersion"],
                })
    except Exception as e:
        errors.append(f"consent: {e}")

    # ── 5. Anonymise orders (keep financial records, scrub PII) ───────────────
    try:
        orders_table = dynamodb.Table(ORDERS_TABLE)
        resp = orders_table.scan(
            FilterExpression=boto3.dynamodb.conditions.Attr("userId").eq(user_sub)
        )
        for order in resp.get("Items", []):
            orders_table.update_item(
                Key={"sessionId": order["sessionId"]},
                UpdateExpression="SET userId = :t, customerEmail = :t",
                ExpressionAttributeValues={":t": TOMBSTONE},
            )
    except Exception as e:
        errors.append(f"orders: {e}")

    # ── 6. Write audit record ─────────────────────────────────────────────────
    status_detail = "success" if not errors else f"partial errors: {'; '.join(errors)}"
    _write_audit(user_sub, "account:deleted", status_detail)

    if errors:
        # Return 207 so the client knows deletion was partial; log the errors
        return {
            "statusCode": 207,
            "headers":    HEADERS,
            "body":       json.dumps({
                "message": "Account partially deleted. Some data could not be removed automatically.",
                "errors":  errors,
            }),
        }

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps({
            "message": "Your account and associated personal data have been deleted. "
                       "Financial transaction records are retained as required by law "
                       "but have been anonymised.",
        }),
    }
