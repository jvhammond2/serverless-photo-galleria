"""
ConsentFunction — src/consent/app.py
--------------------------------------
POST /consent   Record or update user's consent
GET  /consent   Retrieve user's current consent record

GDPR Article 7 requires consent to be:
  - Freely given, specific, informed, and unambiguous
  - Recorded with a timestamp and the exact version of the consent text shown
  - Withdrawable at any time (handled here by storing a revoked record)

Consent record shape in DynamoDB:
  {
    "userId":          "cognito-sub or email",
    "consentVersion":  "1.0",          ← sort key
    "timestamp":       "2026-06-01T…", ← ISO-8601 UTC
    "ip":              "1.2.3.4",      ← from X-Forwarded-For
    "purposes": {
      "functional":    true,           ← always required, cannot be declined
      "analytics":     true/false,
      "marketing":     true/false
    },
    "action":          "grant" | "revoke",
    "expiresAt":       <epoch+7yr>     ← TTL for regulatory retention
  }
"""

import boto3
import json
import os
import time
import uuid
from datetime import datetime, timezone

dynamodb  = boto3.resource("dynamodb")
ssm       = boto3.client("ssm")

CONSENT_TABLE         = os.environ["CONSENT_TABLE"]
AUDIT_LOG_TABLE       = os.environ["AUDIT_LOG_TABLE"]
CONSENT_VERSION_PARAM = os.environ["CONSENT_VERSION_PARAM"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

# 7 years in seconds — minimum retention for GDPR consent records
SEVEN_YEARS = 7 * 365 * 24 * 3600


def _user_id(event: dict) -> str | None:
    """Extract the Cognito sub from the authorizer context."""
    ctx = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email")


def _ip(event: dict) -> str:
    headers = event.get("headers") or {}
    xff = headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else "unknown"


def _current_consent_version() -> str:
    try:
        resp = ssm.get_parameter(Name=CONSENT_VERSION_PARAM)
        return resp["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return "1.0"


def _write_audit(user_id: str, action: str, detail: str):
    table = dynamodb.Table(AUDIT_LOG_TABLE)
    now   = datetime.now(timezone.utc).isoformat()
    table.put_item(Item={
        "auditId":   str(uuid.uuid4()),
        "timestamp": now,
        "userId":    user_id,
        "action":    action,
        "detail":    detail,
        "expiresAt": int(time.time()) + SEVEN_YEARS,
    })


def _error(status: int, msg: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


# ---------------------------------------------------------------------------
# POST /consent — record consent grant or revocation
# ---------------------------------------------------------------------------
def _handle_post(event: dict, user_id: str) -> dict:
    try:
        body     = json.loads(event.get("body") or "{}")
        action   = body.get("action", "grant").lower()
        purposes = body.get("purposes", {})
    except (json.JSONDecodeError, ValueError):
        return _error(400, "Invalid JSON body.")

    if action not in ("grant", "revoke"):
        return _error(400, "'action' must be 'grant' or 'revoke'.")

    # functional consent is non-optional — the site literally doesn't work without it
    purposes["functional"] = True

    version = _current_consent_version()
    now     = datetime.now(timezone.utc).isoformat()

    record = {
        "userId":          user_id,
        "consentVersion":  version,
        "timestamp":       now,
        "ip":              _ip(event),
        "purposes":        purposes,
        "action":          action,
        "expiresAt":       int(time.time()) + SEVEN_YEARS,
    }

    dynamodb.Table(CONSENT_TABLE).put_item(Item=record)
    _write_audit(user_id, f"consent:{action}", f"version={version} purposes={json.dumps(purposes)}")

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps({
            "message":         f"Consent {action}ed.",
            "consentVersion":  version,
            "timestamp":       now,
        }),
    }


# ---------------------------------------------------------------------------
# GET /consent — return the user's latest consent record
# ---------------------------------------------------------------------------
def _handle_get(user_id: str) -> dict:
    table = dynamodb.Table(CONSENT_TABLE)

    # Query all consent records for this user, return the most recent
    resp  = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("userId").eq(user_id),
        ScanIndexForward=False,   # newest first
        Limit=1,
    )
    items = resp.get("Items", [])

    current_version = _current_consent_version()

    if not items:
        return {
            "statusCode": 200,
            "headers":    HEADERS,
            "body":       json.dumps({
                "hasConsent":      False,
                "currentVersion":  current_version,
            }),
        }

    latest = items[0]
    _write_audit(user_id, "consent:read", f"version={latest.get('consentVersion')}")

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps({
            "hasConsent":         latest.get("action") == "grant",
            "consentVersion":     latest.get("consentVersion"),
            "currentVersion":     current_version,
            "needsRenewal":       latest.get("consentVersion") != current_version,
            "timestamp":          latest.get("timestamp"),
            "purposes":           latest.get("purposes", {}),
        }),
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
def handler(event, context):
    user_id = _user_id(event)
    if not user_id:
        return _error(401, "Missing or invalid Authorization token.")

    method = event.get("httpMethod", "").upper()

    if method == "POST":
        return _handle_post(event, user_id)
    elif method == "GET":
        return _handle_get(user_id)
    else:
        return _error(405, f"Method {method} not allowed.")
