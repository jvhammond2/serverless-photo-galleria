import json
import os
import boto3
import stripe

CORS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Headers": "Content-Type,Stripe-Signature",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
}

def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    # Load secrets from SSM
    ssm = boto3.client("ssm")
    stripe.api_key   = ssm.get_parameter(
        Name=os.environ["STRIPE_SECRET_KEY_PARAM"], WithDecryption=True
    )["Parameter"]["Value"]
    webhook_secret   = ssm.get_parameter(
        Name=os.environ["STRIPE_WEBHOOK_SECRET_PARAM"], WithDecryption=True
    )["Parameter"]["Value"]

    # Verify Stripe signature
    sig_header = (event.get("headers") or {}).get("Stripe-Signature", "")
    payload    = event.get("body", "")

    try:
        stripe_event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        return {"statusCode": 400, "headers": CORS, "body": json.dumps({"error": "Invalid signature"})}
    except Exception as e:
        return {"statusCode": 400, "headers": CORS, "body": json.dumps({"error": str(e)})}

    # Handle checkout.session.completed
    if stripe_event["type"] == "checkout.session.completed":
        session = stripe_event["data"]["object"]
        meta    = session.get("metadata") or {}

        photo_id       = meta.get("photoId", "")
        tier           = meta.get("tier", "")
        file_name      = meta.get("fileName", "")
        customer_email = session.get("customer_email", "")
        amount_total   = session.get("amount_total", 0)
        session_id     = session.get("id", "")

        if photo_id and tier and session_id:
            table = boto3.resource("dynamodb").Table(os.environ["ORDERS_TABLE"])
            try:
                table.put_item(
                    Item={
                        "sessionId":     session_id,
                        "photoId":       photo_id,
                        "tier":          tier,
                        "fileName":      file_name,
                        "customerEmail": customer_email,
                        "amountTotal":   amount_total,
                        "status":        "paid",
                        "createdAt":     str(session.get("created", "")),
                        "source":        "webhook",
                    },
                    ConditionExpression="attribute_not_exists(sessionId)",
                )
            except Exception:
                pass  # Idempotent — already recorded is fine

    return {"statusCode": 200, "headers": CORS, "body": json.dumps({"received": True})}
