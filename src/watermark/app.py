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
"""

import io
import os

import boto3
from PIL import Image, ImageDraw, ImageFont

s3 = boto3.client("s3")

ORIGINALS_BUCKET = os.environ.get("ORIGINALS_BUCKET", "")
THUMBS_BUCKET    = os.environ["THUMBS_BUCKET"]
WATERMARK_TEXT   = os.environ.get("WATERMARK_TEXT", "© Galleria")


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


def handler(event, context):
    try:
        source_bucket = event.get("s3Bucket") or ORIGINALS_BUCKET
        object_key    = event.get("s3Key")

        if not object_key:
            raise ValueError("s3Key is required in the event payload")

        print(f"Watermarking {object_key} from {source_bucket}")

        obj        = s3.get_object(Bucket=source_bucket, Key=object_key)
        image_data = obj["Body"].read()

        with Image.open(io.BytesIO(image_data)) as img:
            if img.mode not in ("RGB",):
                img = img.convert("RGB")

            watermarked = apply_watermark(img, WATERMARK_TEXT)

            buffer = io.BytesIO()
            watermarked.save(buffer, format="JPEG", quality=88, optimize=True)
            buffer.seek(0)

        output_key = f"watermarked-{object_key}"
        s3.put_object(
            Bucket=THUMBS_BUCKET,
            Key=output_key,
            Body=buffer,
            ContentType="image/jpeg",
        )

        print(f"Saved watermarked image to {THUMBS_BUCKET}/{output_key}")

        return {
            "statusCode": 200,
            "body": f"Watermarked {object_key} saved to {THUMBS_BUCKET}/{output_key}",
        }

    except Exception as e:
        print(f"Error watermarking image: {str(e)}")
        raise e
