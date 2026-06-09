"""
WatermarkFunction — src/watermark/app.py
-----------------------------------------
Standalone watermark Lambda (invoked directly, outside the main pipeline).
Reads from OriginalsS3Bucket, writes watermarked copy to ThumbsS3Bucket.

Uses Pillow 10's ImageFont.load_default(size=) so no font files are needed
in the Lambda deployment package.

Environment variables:
  ORIGINALS_BUCKET  — source bucket
  THUMBS_BUCKET     — destination bucket
  WATERMARK_TEXT    — text to stamp (default: "© Galleria")
  COPYRIGHT_OWNER   — name written into EXIF Copyright field (default: "Galleria")
"""

import io
import os

import boto3
import piexif
from aws_xray_sdk.core import patch_all, xray_recorder
from PIL import Image, ImageDraw, ImageFont
patch_all()

s3 = boto3.client("s3")

ORIGINALS_BUCKET = os.environ.get("ORIGINALS_BUCKET", "")
THUMBS_BUCKET    = os.environ["THUMBS_BUCKET"]
WATERMARK_TEXT   = os.environ.get("WATERMARK_TEXT", "© Galleria")
COPYRIGHT_OWNER  = os.environ.get("COPYRIGHT_OWNER", "Galleria")


def apply_watermark(img: Image.Image, text: str) -> Image.Image:
    """Render semi-transparent text in the bottom-right corner."""
    w, h      = img.size
    font_size = max(24, min(w, h) // 22)
    font      = ImageFont.load_default(size=font_size)

    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox  = dummy.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    base    = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    margin = max(16, font_size // 2)
    x = w - tw - margin
    y = h - th - margin

    # Drop shadow then main text
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 140))
    draw.text((x, y),         text, font=font, fill=(255, 255, 255, 210))

    return Image.alpha_composite(base, overlay).convert("RGB")


def build_exif(copyright_owner: str, watermark_text: str) -> bytes:
    """
    Build a minimal EXIF blob with Copyright and Artist tags.

    AWS Cert Note (DVA-C02): this metadata travels inside the JPEG file itself
    — it survives S3 download and is visible in any EXIF viewer.  It does NOT
    replace a legal watermark but establishes chain-of-custody evidence.

    EXIF tag reference:
      0x013B  Artist        — photographer/studio name
      0x8298  Copyright     — copyright notice string
      0x0131  Software      — processing tool identifier
    """
    import datetime
    year = datetime.date.today().year
    notice = f"© {year} {copyright_owner}. All rights reserved."

    zeroth = {
        piexif.ImageIFD.Artist:    notice.encode("utf-8"),
        piexif.ImageIFD.Copyright: notice.encode("utf-8"),
        piexif.ImageIFD.Software:  b"Galleria Watermark Lambda",
    }
    try:
        return piexif.dump({"0th": zeroth, "Exif": {}, "GPS": {}})
    except Exception:
        return b""


def handler(event, context):
    try:
        record    = event["Records"][0]
        s3_bucket = record["s3"]["bucket"]["name"]
        s3_key    = record["s3"]["object"]["key"]
    except (KeyError, IndexError):
        # Direct invocation (e.g., test or Step Functions)
        s3_bucket = event.get("s3Bucket", ORIGINALS_BUCKET)
        s3_key    = event.get("s3Key", "")

    if not s3_key:
        print("[watermark] No s3Key — nothing to do")
        return {**event, "watermarked": False}

    print(f"[watermark] Processing s3://{s3_bucket}/{s3_key}")

    # Download original
    obj      = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    img_data = obj["Body"].read()

    with Image.open(io.BytesIO(img_data)) as img:
        watermarked = apply_watermark(img, WATERMARK_TEXT)

    # Inject EXIF copyright metadata
    exif_bytes = build_exif(COPYRIGHT_OWNER, WATERMARK_TEXT)
    out_buf    = io.BytesIO()
    save_kwargs = {"format": "JPEG", "quality": 92, "optimize": True}
    if exif_bytes:
        save_kwargs["exif"] = exif_bytes
    watermarked.save(out_buf, **save_kwargs)
    out_buf.seek(0)

    # Write to THUMBS_BUCKET under same key
    dest_key = s3_key
    s3.put_object(
        Bucket=THUMBS_BUCKET,
        Key=dest_key,
        Body=out_buf.read(),
        ContentType="image/jpeg",
    )

    try:
        seg = xray_recorder.current_subsegment() or xray_recorder.current_segment()
        if seg:
            seg.put_annotation("s3Key", s3_key)
            seg.put_annotation("destBucket", THUMBS_BUCKET)
    except Exception:
        pass

    print(f"[watermark] Done: s3://{THUMBS_BUCKET}/{dest_key}")
    return {**event, "watermarked": True, "watermarkKey": dest_key}
