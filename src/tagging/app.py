"""
TaggingFunction — src/tagging/app.py
-------------------------------------
Invoked by the Step Functions photo pipeline as its final stage.

Expected input event (passed from previous pipeline steps):
{
    "photoId":      "uuid-string",
    "originalKey":  "originals/uuid.jpg",
    "thumbnailKey": "thumbs/uuid.jpg",
    "previewKey":   "previews/uuid.jpg"
}

Responsibilities:
  1. Run Amazon Rekognition DetectLabels on the original image.
  2. Compute a perceptual hash (pHash) for fingerprinting / duplicate detection.
  3. Extract dominant color palette from the thumbnail via Pillow quantize.
  4. Write a complete metadata record to PhotoMetadataTable so the
     customer-facing search / gallery endpoints have everything they need.
  5. Return the enriched event so Step Functions can log / chain further.
"""

import colorsys
import io
import boto3
import json
import os
from datetime import datetime, timezone

import imagehash
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS

from aws_xray_sdk.core import patch_all, xray_recorder
patch_all()

rekognition = boto3.client("rekognition")
s3          = boto3.client("s3")
dynamodb    = boto3.resource("dynamodb")

METADATA_TABLE   = os.environ["METADATA_TABLE"]
ORIGINALS_BUCKET = os.environ["ORIGINALS_BUCKET"]   # raw photographer uploads
THUMBS_BUCKET    = os.environ["THUMBS_BUCKET"]       # thumbnails used for palette extraction


# ── Color mood classification ─────────────────────────────────────────────────
# Maps the dominant palette color to a browsable mood tag.
# AWS Cert Note (MLA-C01): This is deterministic ML pre-processing — no model
# inference needed. Storing the result at ingest time means zero compute cost
# at query time when filtering by color mood in the discovery UI.

def _hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _classify_mood(r: int, g: int, b: int) -> str:
    """Return one of: warm | cool | green | purple | neutral | dark | light."""
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hue_deg = h * 360
    if l < 0.18:
        return "dark"
    if l > 0.87:
        return "light"
    if s < 0.15:
        return "neutral"
    if hue_deg < 30 or hue_deg >= 330:
        return "warm"    # reds
    if hue_deg < 75:
        return "warm"    # oranges / yellows
    if hue_deg < 165:
        return "green"
    if hue_deg < 255:
        return "cool"    # blues / cyans
    return "purple"


def _extract_palette(img_bytes: bytes, n_colors: int = 8) -> tuple[list[str], str]:
    """
    Extract the top-3 dominant hex colors and overall color mood from image bytes.

    Uses Pillow's built-in median-cut quantize (fast, no extra dependencies).
    Returns (dominant_colors_list, mood_string).
    """
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            # Resize for speed; LANCZOS gives the best colour representation
            small = img.convert("RGB").resize((100, 100), Image.LANCZOS)
            # Quantize to n_colors using median-cut algorithm
            quantized = small.quantize(colors=n_colors, method=Image.Quantize.MEDIANCUT)
            palette_raw = quantized.getpalette()          # flat [R,G,B, R,G,B, ...]

        # Count pixel frequency per palette index
        pixel_data = list(quantized.getdata())
        freq: dict[int, int] = {}
        for idx in pixel_data:
            freq[idx] = freq.get(idx, 0) + 1

        # Sort palette entries by frequency descending
        sorted_indices = sorted(freq, key=lambda i: freq[i], reverse=True)

        top_colors: list[str] = []
        for i in sorted_indices[:3]:
            r, g, b = palette_raw[i * 3], palette_raw[i * 3 + 1], palette_raw[i * 3 + 2]
            top_colors.append(_hex(r, g, b))

        # Mood = classification of the single most-frequent color
        ti = sorted_indices[0]
        mood = _classify_mood(palette_raw[ti * 3], palette_raw[ti * 3 + 1], palette_raw[ti * 3 + 2])
        return top_colors, mood
    except Exception as e:
        print(f"Palette extraction failed (non-fatal): {e}")
        return [], "neutral"


# ── GPS / EXIF extraction ─────────────────────────────────────────────────────
# AWS Cert Note (SAA-C03): Storing GPS coordinates at ingest time means zero
# additional API calls at query time — lat/lng live in DynamoDB alongside
# the rest of the metadata and are returned by the search Lambda for free.
# A DynamoDB GSI on a geohash would enable bounding-box queries at scale,
# but a simple lat/lng scan filter is sufficient for MVP.

def _dms_to_decimal(dms, ref: str) -> float | None:
    """Convert (degrees, minutes, seconds) rational EXIF values to decimal degrees."""
    if not dms or len(dms) < 3:
        return None

    def _rat(v) -> float:
        """Handle IFDRational, (num, denom) tuple, or plain numeric."""
        if hasattr(v, "numerator") and hasattr(v, "denominator"):
            return float(v.numerator) / float(v.denominator) if v.denominator else 0.0
        if isinstance(v, tuple) and len(v) == 2:
            return float(v[0]) / float(v[1]) if v[1] else 0.0
        return float(v)

    degrees = _rat(dms[0]) + _rat(dms[1]) / 60.0 + _rat(dms[2]) / 3600.0
    if ref in ("S", "W"):
        degrees = -degrees
    return round(degrees, 6)


def _extract_gps(img_bytes: bytes) -> tuple[float | None, float | None]:
    """
    Return (latitude, longitude) decimal degrees from JPEG EXIF, or (None, None).

    Uses Pillow's _getexif() — works on JPEG/TIFF; returns (None, None) silently
    for PNG, WebP, and images without GPS tags.
    """
    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            exif_raw = getattr(img, "_getexif", lambda: None)()
            if not exif_raw:
                return None, None

            gps_raw = None
            for tag_id, value in exif_raw.items():
                if TAGS.get(tag_id) == "GPSInfo":
                    gps_raw = {GPSTAGS.get(k, k): v for k, v in value.items()}
                    break

            if not gps_raw:
                return None, None

            lat = _dms_to_decimal(gps_raw.get("GPSLatitude"),
                                   gps_raw.get("GPSLatitudeRef", "N"))
            lng = _dms_to_decimal(gps_raw.get("GPSLongitude"),
                                   gps_raw.get("GPSLongitudeRef", "E"))
            return lat, lng
    except Exception as e:
        print(f"GPS extraction failed (non-fatal): {e}")
        return None, None


def handler(event, context):
    photo_id        = event["photoId"]
    original_key    = event["originalKey"]
    thumbnail_key   = event["thumbnailKey"]
    preview_key     = event["previewKey"]
    photographer_id    = event.get("photographerId", "")
    category           = event.get("category", "other")
    color_mood_override = event.get("colorMood", "").strip().lower()  # manual override
    file_name          = original_key.rsplit("/", 1)[-1]

    # ── 1. Rekognition label detection ───────────────────────────────────────
    reko_resp = rekognition.detect_labels(
        Image={"S3Object": {"Bucket": ORIGINALS_BUCKET, "Name": original_key}},
        MaxLabels=25,
        MinConfidence=70,
    )

    tags = [label["Name"].lower() for label in reko_resp.get("Labels", [])]

    # ── 2. Perceptual hash (pHash) + dominant palette ────────────────────────
    # AWS Cert Note (DVA-C02 / SAA-C03): pHash is a compact 64-bit fingerprint
    # of an image's visual content.  Two images with pHash Hamming distance ≤ 10
    # are perceptually similar — useful for detecting stolen / re-compressed
    # copies even after colour correction or slight cropping.
    # The hash string (e.g. "f8e0c08080c0e0f8") is stored as a plain DynamoDB
    # string.  At query time you load both hashes and call imagehash.hex_to_hash()
    # to compare them — no specialised vector index needed at MVP scale.
    p_hash = ""
    dominant_colors: list[str] = []
    color_mood = "neutral"
    obj_body: bytes | None = None
    try:
        obj_body = s3.get_object(Bucket=ORIGINALS_BUCKET, Key=original_key)["Body"].read()
        with Image.open(io.BytesIO(obj_body)) as img:
            p_hash = str(imagehash.phash(img))
        print(f"pHash for {photo_id}: {p_hash}")
    except Exception as e:
        # Fail-open: a missing hash is annoying but not fatal
        print(f"pHash computation failed (non-fatal): {e}")

    # Extract palette + GPS from the thumbnail / original
    # Re-use the already-read obj_body for GPS (avoids a second S3 GET on originals)
    gps_lat: float | None = None
    gps_lng: float | None = None
    try:
        if obj_body:   # set above during pHash computation
            gps_lat, gps_lng = _extract_gps(obj_body)
            if gps_lat is not None:
                print(f"GPS for {photo_id}: ({gps_lat}, {gps_lng})")
    except Exception as e:
        print(f"GPS extraction failed (non-fatal): {e}")

    try:
        thumb_bytes = s3.get_object(Bucket=THUMBS_BUCKET, Key=thumbnail_key)["Body"].read()
        dominant_colors, color_mood = _extract_palette(thumb_bytes)
        print(f"Palette for {photo_id}: colors={dominant_colors}, mood={color_mood}")
    except Exception as e:
        print(f"Palette extraction S3 read failed (non-fatal): {e}")
    # Photographer-provided override takes precedence over auto-detected mood
    VALID_MOODS = {"warm","cool","green","purple","neutral","dark","light"}
    if color_mood_override in VALID_MOODS:
        color_mood = color_mood_override
        print(f"Color mood overridden to: {color_mood}")

    # ── 3. Write metadata record ──────────────────────────────────────────────
    now_iso = datetime.now(timezone.utc).isoformat()

    table = dynamodb.Table(METADATA_TABLE)
    table.put_item(
        Item={
            "photoId":        photo_id,
            "originalKey":    original_key,
            "thumbnailKey":   thumbnail_key,
            "previewKey":     preview_key,
            "tags":           tags,
            "likes":          0,
            "uploadDate":     now_iso,
            "uploadedAt":     now_iso,    # GSI sort key for category-uploadedAt-index
            "category":       category,  # GSI partition key
            "title":          file_name,
            "fileName":       file_name,
            "photographerId": photographer_id,
            "status":         "active",
            "pHash":          p_hash,
            "dominantColors": dominant_colors,
            "colorMood":      color_mood,
            # GPS fields omitted when None (DynamoDB rejects None values)
            **( {"gpsLat": str(gps_lat), "gpsLng": str(gps_lng)}
                if gps_lat is not None and gps_lng is not None else {} ),
        }
    )

    # AWS Cert Note (DVA-C02): Annotations are indexed; use them for values you
    # want to filter on in X-Ray (photoId, category). Metadata is free-form
    # but not searchable — use it for tag lists or large blobs.
    _annotate(photoId=photo_id, photographerId=photographer_id, category=category, pHash=p_hash)
    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        if seg:
            seg.put_metadata("tags", tags, namespace="galleria")
    except Exception:
        pass

    print(f"Metadata stored for photoId={photo_id}, tags={tags}")

    # ── 5. Return enriched event for Step Functions chaining ─────────────────
    return {**event, "tags": tags, "pHash": p_hash,
            "dominantColors": dominant_colors, "colorMood": color_mood,
            "gpsLat": gps_lat, "gpsLng": gps_lng}


def _annotate(**kwargs):
    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        if seg:
            for k, v in kwargs.items():
                seg.put_annotation(k, str(v))
    except Exception:
        pass
