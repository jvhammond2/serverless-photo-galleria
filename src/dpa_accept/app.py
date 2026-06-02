"""
DpaAcceptFunction — src/dpa_accept/app.py
-------------------------------------------
POST /dpa-accept   Record photographer's acceptance of the Data Processing Agreement
GET  /dpa-accept   Check current DPA acceptance status

GDPR Article 28 — Controller / Processor relationship.

When a photographer uploads photos, you (the platform) become a data processor
acting on their behalf.  GDPR requires a written Data Processing Agreement (DPA)
between the controller (photographer) and processor (you).  This endpoint
records the photographer's acceptance of the DPA, including:

  - The DPA version they accepted
  - The timestamp and their IP address
  - Whether they are operating under GDPR jurisdiction

The DPA version is stored in SSM Parameter Store at:
  /serverless-photo-galleria/dpa-version

Update that parameter whenever you revise the DPA text.  Photographers whose
stored version doesn't match the current version will be prompted to re-accept
before they can upload new photos (enforce this check in GetUploadUrlFunction).

Acceptance record is written to PhotographerProfileTable so it travels with
the photographer's profile across all operations.
"""

import boto3
import json
import os
import time
import uuid
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
ssm      = boto3.client("ssm")

PROFILE_TABLE    = os.environ["PROFILE_TABLE"]
AUDIT_LOG_TABLE  = os.environ["AUDIT_LOG_TABLE"]
DPA_VERSION_PARAM = os.environ["DPA_VERSION_PARAM"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}

SEVEN_YEARS = 7 * 365 * 24 * 3600


def _photographer_id(event: dict) -> tuple[str | None, str | None]:
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub"), claims.get("email")


def _ip(event: dict) -> str:
    headers = event.get("headers") or {}
    xff = headers.get("X-Forwarded-For", "")
    return xff.split(",")[0].strip() if xff else "unknown"


def _current_dpa_version() -> str:
    try:
        resp = ssm.get_parameter(Name=DPA_VERSION_PARAM)
        return resp["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return "1.0"


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


# ---------------------------------------------------------------------------
# POST /dpa-accept — record acceptance
# ---------------------------------------------------------------------------
def _handle_post(event: dict, photographer_id: str, email: str | None) -> dict:
    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, ValueError):
        return _error(400, "Invalid JSON body.")

    # Optional: photographer can declare their jurisdiction for GDPR applicability
    jurisdiction = body.get("jurisdiction", "unknown").upper()[:10]

    version = _current_dpa_version()
    now     = datetime.now(timezone.utc).isoformat()

    dpa_record = {
        "dpaVersion":      version,
        "acceptedAt":      now,
        "acceptedFromIp":  _ip(event),
        "jurisdiction":    jurisdiction,
    }

    # Upsert into PhotographerProfile — creates the profile if it doesn't exist yet
    profile_table = dynamodb.Table(PROFILE_TABLE)
    profile_table.update_item(
        Key={"profileId": photographer_id},
        UpdateExpression=(
            "SET dpaAcceptance = :dpa, "
            "email = if_not_exists(email, :email), "
            "updatedAt = :now"
        ),
        ExpressionAttributeValues={
            ":dpa":   dpa_record,
            ":email": email or "unknown",
            ":now":   now,
        },
    )

    _write_audit(
        photographer_id,
        "dpa:accepted",
        f"version={version} jurisdiction={jurisdiction} ip={_ip(event)}",
    )

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps({
            "message":     "Data Processing Agreement accepted.",
            "dpaVersion":  version,
            "acceptedAt":  now,
        }),
    }


# ---------------------------------------------------------------------------
# GET /dpa-accept — check acceptance status
# ---------------------------------------------------------------------------
def _handle_get(photographer_id: str) -> dict:
    resp    = dynamodb.Table(PROFILE_TABLE).get_item(Key={"profileId": photographer_id})
    profile = resp.get("Item", {})
    acceptance = profile.get("dpaAcceptance", {})

    current_version = _current_dpa_version()
    accepted_version = acceptance.get("dpaVersion")

    return {
        "statusCode": 200,
        "headers":    HEADERS,
        "body":       json.dumps({
            "hasAccepted":    bool(accepted_version),
            "acceptedVersion": accepted_version,
            "currentVersion": current_version,
            "needsRenewal":   accepted_version != current_version,
            "acceptedAt":     acceptance.get("acceptedAt"),
        }),
    }


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
def handler(event, context):
    photographer_id, email = _photographer_id(event)
    if not photographer_id:
        return _error(401, "Missing or invalid Authorization token.")

    method = event.get("httpMethod", "").upper()

    if method == "POST":
        return _handle_post(event, photographer_id, email)
    elif method == "GET":
        return _handle_get(photographer_id)
    else:
        return _error(405, f"Method {method} not allowed.")
