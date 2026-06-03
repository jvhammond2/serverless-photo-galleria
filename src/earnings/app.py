"""
EarningsDashboardFunction — src/earnings/app.py
-------------------------------------------------
GET /earnings
GET /earnings?period=2026-06     ← filter to a specific YYYY-MM
GET /earnings?photoId=abc123     ← drill into a single photo

Returns a photographer's revenue dashboard scoped to their profileId (Cognito
sub).  Only shows earnings for photos that belong to the authenticated
photographer — the check is done by scanning PhotoMetadataTable filtered on
`photographerId`.

Response shape:
{
  "period":       "2026-06"  | "all-time",
  "summary": {
    "totalRevenue":   125.00,
    "totalSales":     8,
    "totalLikes":     42,
    "totalPhotos":    15
  },
  "topPhotos": [            ← top 5 by revenue
    {
      "photoId":    "...",
      "title":      "Sunset over Dublin",
      "sales":      3,
      "revenue":    37.50,
      "likes":      12,
      "thumbnailKey": "..."
    },
    ...
  ],
  "monthly": {              ← only present when period="all-time"
    "2026-05": { "revenue": 50.00, "sales": 4 },
    "2026-06": { "revenue": 75.00, "sales": 4 }
  }
}

Implementation notes:
  - PhotoMetadataTable is scanned for the photographer's photos (small table
    for a portfolio app; add a GSI on photographerId in a future iteration
    if the catalog grows large).
  - OrdersTable is scanned for completed (status=completed) orders that
    contain one of the photographer's photoIds.
  - No external financial system is queried — Stripe settlement data is not
    surfaced here.  Revenue figures are based on the price stored at order time.
"""

import boto3
import json
import os
from collections import defaultdict
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")

METADATA_TABLE = os.environ["METADATA_TABLE"]
ORDERS_TABLE   = os.environ["ORDERS_TABLE"]
PROFILE_TABLE  = os.environ["PROFILE_TABLE"]

HEADERS = {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
}


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def _photographer_id(event: dict) -> str | None:
    ctx    = (event.get("requestContext") or {}).get("authorizer") or {}
    claims = ctx.get("claims") or {}
    return claims.get("sub") or claims.get("email")


def _error(status: int, msg: str) -> dict:
    return {"statusCode": status, "headers": HEADERS, "body": json.dumps({"error": msg})}


def handler(event, context):
    photographer_id = _photographer_id(event)
    if not photographer_id:
        return _error(401, "Missing or invalid Authorization token.")

    params   = event.get("queryStringParameters") or {}
    period   = params.get("period", "").strip()    # e.g. "2026-06"  or ""
    photo_filter = params.get("photoId", "").strip()

    # ── 1. Fetch all photos owned by this photographer ─────────────────────
    meta_table = dynamodb.Table(METADATA_TABLE)
    scan_kwargs = {
        "FilterExpression": boto3.dynamodb.conditions.Attr("photographerId").eq(photographer_id),
        "ProjectionExpression": "photoId, title, likes, thumbnailKey, #st",
        "ExpressionAttributeNames": {"#st": "status"},
    }
    if photo_filter:
        scan_kwargs["FilterExpression"] = (
            boto3.dynamodb.conditions.Attr("photographerId").eq(photographer_id)
            & boto3.dynamodb.conditions.Attr("photoId").eq(photo_filter)
        )

    photos = []
    resp = meta_table.scan(**scan_kwargs)
    photos.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = meta_table.scan(**scan_kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
        photos.extend(resp.get("Items", []))

    if not photos:
        return {
            "statusCode": 200,
            "headers": HEADERS,
            "body": json.dumps({
                "period":  period or "all-time",
                "summary": {"totalRevenue": 0, "totalSales": 0, "totalLikes": 0, "totalPhotos": 0},
                "topPhotos": [],
            }),
        }

    photo_ids  = {p["photoId"] for p in photos}
    photo_map  = {p["photoId"]: p for p in photos}

    # ── 2. Fetch completed orders containing any of the photographer's photos ─
    orders_table = dynamodb.Table(ORDERS_TABLE)
    filter_expr  = boto3.dynamodb.conditions.Attr("status").eq("paid")

    all_orders = []
    resp = orders_table.scan(FilterExpression=filter_expr)
    all_orders.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = orders_table.scan(FilterExpression=filter_expr, ExclusiveStartKey=resp["LastEvaluatedKey"])
        all_orders.extend(resp.get("Items", []))

    # ── 3. Aggregate per-photo and per-month ───────────────────────────────
    photo_stats = defaultdict(lambda: {"sales": 0, "revenue": Decimal("0")})
    monthly     = defaultdict(lambda: {"sales": 0, "revenue": Decimal("0")})

    for order in all_orders:
        order_month = (order.get("createdAt") or "")[:7]  # "YYYY-MM"
        if period and order_month != period:
            continue

        # An order may contain multiple line items (cart purchase)
        items = order.get("items") or []
        if not items:
            # Single-photo order stored directly on the record
            pid   = order.get("photoId")
            price = Decimal(str(order.get("amount", 0))) / 100   # cents → dollars
            if pid and pid in photo_ids:
                photo_stats[pid]["sales"]   += 1
                photo_stats[pid]["revenue"] += price
                monthly[order_month]["sales"]   += 1
                monthly[order_month]["revenue"] += price
        else:
            for line in items:
                pid   = line.get("photoId")
                price = Decimal(str(line.get("price", 0))) / 100
                if pid and pid in photo_ids:
                    photo_stats[pid]["sales"]   += 1
                    photo_stats[pid]["revenue"] += price
                    monthly[order_month]["sales"]   += 1
                    monthly[order_month]["revenue"] += price

    # ── 4. Build top-photos list ───────────────────────────────────────────
    top_photos = []
    for pid, stats in photo_stats.items():
        meta = photo_map.get(pid, {})
        top_photos.append({
            "photoId":      pid,
            "title":        meta.get("title", pid),
            "thumbnailKey": meta.get("thumbnailKey", ""),
            "likes":        int(meta.get("likes", 0)),
            "sales":        stats["sales"],
            "revenue":      float(round(stats["revenue"], 2)),
        })
    # Sort by revenue descending, cap at top 10
    top_photos.sort(key=lambda x: x["revenue"], reverse=True)
    top_photos = top_photos[:10]

    # Include photos with no sales so the photographer sees their full catalog
    sold_ids = {p["photoId"] for p in top_photos}
    for pid, meta in photo_map.items():
        if pid not in sold_ids:
            top_photos.append({
                "photoId":      pid,
                "title":        meta.get("title", pid),
                "thumbnailKey": meta.get("thumbnailKey", ""),
                "likes":        int(meta.get("likes", 0)),
                "sales":        0,
                "revenue":      0.0,
            })

    # ── 5. Summary totals ─────────────────────────────────────────────────
    total_revenue = sum(s["revenue"] for s in photo_stats.values())
    total_sales   = sum(s["sales"]   for s in photo_stats.values())
    total_likes   = sum(int(p.get("likes", 0)) for p in photos)

    response_body = {
        "period":  period or "all-time",
        "summary": {
            "totalRevenue": float(round(total_revenue, 2)),
            "totalSales":   total_sales,
            "totalLikes":   total_likes,
            "totalPhotos":  len(photos),
        },
        "topPhotos": top_photos,
    }

    # Monthly breakdown only for all-time view
    if not period and monthly:
        response_body["monthly"] = {
            month: {
                "revenue": float(round(data["revenue"], 2)),
                "sales":   data["sales"],
            }
            for month, data in sorted(monthly.items())
        }

    return {
        "statusCode": 200,
        "headers": HEADERS,
        "body": json.dumps(response_body, cls=DecimalEncoder),
    }
