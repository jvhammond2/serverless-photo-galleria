"""
ProcessingFunction — src/processing/app.py
-------------------------------------------
Central image processing engine for the Galleria pipeline.
Invoked by Step Functions after the photographer uploads to OriginalsS3Bucket.

Expected input event:
{
    "s3Bucket": "galleria-originals-...",
    "s3Key":    "my-photo.jpg",
    "actions":  ["exif_rotate", "sharpen", "watermark"]   # ordered list, may be empty
}

Supported actions (applied in the order supplied):
  none          — publish as-is (no pixel changes)
  exif_rotate   — correct phone/camera orientation from EXIF tag
  autoenhance   — auto-contrast + gentle colour and brightness lift
  grayscale     — classic black-and-white
  sepia         — warm vintage tone
  saturate      — vivid, punchy colours
  sharpen       — studio-grade unsharp mask
  blur          — cinematic gaussian soft-focus
  denoise       — median-filter grain reduction
  crop          — centre-square crop
  letterbox     — pad to 3:2 widescreen canvas (black bars)
  rotate        — 90 degrees clockwise
  watermark     — semi-transparent text protection mark
  webp          — convert output format to WebP (applied at save time)

Outputs:
  PREVIEWS_BUCKET  <- full-size processed image  (e.g. previews/<uuid>.jpg)
  THUMBS_BUCKET    <- 300x300 thumbnail           (e.g. thumbs/<uuid>.jpg)

Returns to Step Functions:
  { photoId, originalKey, thumbnailKey, previewKey }
"""

import io
import os
import uuid

import boto3
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

s3       = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

ORIGINALS_BUCKET = os.environ["ORIGINALS_BUCKET"]
THUMBS_BUCKET    = os.environ["THUMBS_BUCKET"]
PREVIEWS_BUCKET  = os.environ["PREVIEWS_BUCKET"]
PROFILE_TABLE    = os.environ.get("PROFILE_TABLE", "")
WATERMARK_TEXT   = os.environ.get("WATERMARK_TEXT", "© Galleria")


def _get_watermark_text(photographer_id: str | None) -> str:
    """Return the photographer's custom watermark text if configured,
    falling back to the stack-level default."""
    if not photographer_id or not PROFILE_TABLE:
        return WATERMARK_TEXT
    try:
        resp    = dynamodb.Table(PROFILE_TABLE).get_item(
            Key={"profileId": photographer_id},
            ProjectionExpression="watermarkText",
        )
        item    = resp.get("Item", {})
        custom  = (item.get("watermarkText") or "").strip()
        return custom if custom else WATERMARK_TEXT
    except Exception:
        return WATERMARK_TEXT

THUMB_SIZE = (300, 300)


# ---------------------------------------------------------------------------
# Effect implementations
# ---------------------------------------------------------------------------

def effect_exif_rotate(img: Image.Image) -> Image.Image:
    """Correct orientation stored in EXIF (phones always need this)."""
    return ImageOps.exif_transpose(img)


def effect_autoenhance(img: Image.Image) -> Image.Image:
    """Auto-contrast + gentle colour and brightness lift."""
    img = ImageOps.autocontrast(img, cutoff=0.5)
    img = ImageEnhance.Color(img).enhance(1.2)
    img = ImageEnhance.Brightness(img).enhance(1.05)
    img = ImageEnhance.Contrast(img).enhance(1.1)
    return img


def effect_grayscale(img: Image.Image) -> Image.Image:
    """Convert to black-and-white, keeping RGB mode for JPEG compatibility."""
    return img.convert("L").convert("RGB")


def effect_sepia(img: Image.Image) -> Image.Image:
    """Warm vintage sepia tone."""
    grey = img.convert("L")
    r = grey.point(lambda p: min(int(p * 1.12), 255))
    g = grey.point(lambda p: int(p * 0.88))
    b = grey.point(lambda p: max(int(p * 0.70), 0))
    return Image.merge("RGB", (r, g, b))


def effect_saturate(img: Image.Image) -> Image.Image:
    """Boost colour saturation for vivid, punchy results."""
    return ImageEnhance.Color(img).enhance(1.7)


def effect_sharpen(img: Image.Image) -> Image.Image:
    """Unsharp mask — the standard photographic sharpening tool."""
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))


def effect_blur(img: Image.Image) -> Image.Image:
    """Cinematic gaussian soft-focus."""
    return img.filter(ImageFilter.GaussianBlur(radius=10))


def effect_denoise(img: Image.Image) -> Image.Image:
    """Median filter to soften high-ISO sensor noise."""
    return img.filter(ImageFilter.MedianFilter(size=3))


def effect_crop(img: Image.Image) -> Image.Image:
    """Centre-square crop — removes equal amounts from the longer dimension."""
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top  = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def effect_letterbox(img: Image.Image) -> Image.Image:
    """Pad image to a consistent 3:2 widescreen canvas with black bars."""
    w, h   = img.size
    target = (max(w, int(h * 1.5)), max(h, int(w / 1.5)))
    return ImageOps.pad(img, target, color=(0, 0, 0))


def effect_rotate(img: Image.Image) -> Image.Image:
    """Rotate 90 degrees clockwise."""
    return img.rotate(270, expand=True)


def effect_watermark(img: Image.Image, watermark_text: str = "") -> Image.Image:
    """Semi-transparent text watermark in the bottom-right corner.
    Uses Pillow 10's load_default(size=) so no font files are required.
    """
    text = watermark_text or WATERMARK_TEXT
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

    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 140))
    draw.text((x, y),         text, font=font, fill=(255, 255, 255, 210))

    return Image.alpha_composite(base, overlay).convert("RGB")


# ---------------------------------------------------------------------------
# Effect dispatch table
# ---------------------------------------------------------------------------

EFFECTS = {
    "exif_rotate": effect_exif_rotate,
    "autoenhance": effect_autoenhance,
    "grayscale":   effect_grayscale,
    "sepia":       effect_sepia,
    "saturate":    effect_saturate,
    "sharpen":     effect_sharpen,
    "blur":        effect_blur,
    "denoise":     effect_denoise,
    "crop":        effect_crop,
    "letterbox":   effect_letterbox,
    "rotate":      effect_rotate,
    "watermark":   effect_watermark,
}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event, context):
    s3_bucket       = event["s3Bucket"]
    s3_key          = event["s3Key"]
    actions         = event.get("actions", [])
    photographer_id = event.get("photographerId")
    photo_id        = str(uuid.uuid4())

    # Resolve watermark text locally — avoids mutating module-level global
    watermark_text = _get_watermark_text(photographer_id)

    print(f"Processing photoId={photo_id}  key={s3_key}  actions={actions}  watermark='{watermark_text}'")

    # 1. Download original from S3
    obj        = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    image_data = obj["Body"].read()

    with Image.open(io.BytesIO(image_data)) as src:
        img = src.copy()

    # Normalise to RGB so every downstream save path is consistent
    if img.mode not in ("RGB",):
        img = img.convert("RGB")

    # EXIF rotation is always applied first regardless of its position in the list
    if "exif_rotate" in actions:
        img = effect_exif_rotate(img)

    # "none" short-circuits all other pixel-level effects
    if "none" not in actions:
        for action in actions:
            if action in ("exif_rotate", "webp", "none"):
                continue
            fn = EFFECTS.get(action)
            if fn:
                if action == "watermark":
                    img = fn(img, watermark_text)
                else:
                    img = fn(img)
                print(f"  Applied: {action}")
            else:
                print(f"  Skipped unknown action: {action}")

    # 2. Determine output format
    use_webp     = "webp" in actions
    ext          = "webp"  if use_webp else "jpg"
    save_format  = "WEBP"  if use_webp else "JPEG"
    save_kwargs  = {"quality": 88, "method": 6} if use_webp else {"quality": 88, "optimize": True}
    content_type = "image/webp" if use_webp else "image/jpeg"

    # 3. Full-size preview -> PREVIEWS_BUCKET
    preview_key = f"previews/{photo_id}.{ext}"
    preview_buf = io.BytesIO()
    img.save(preview_buf, format=save_format, **save_kwargs)
    preview_buf.seek(0)
    s3.put_object(Bucket=PREVIEWS_BUCKET, Key=preview_key, Body=preview_buf, ContentType=content_type)

    # 4. Thumbnail -> THUMBS_BUCKET (aspect-preserving, max 300x300)
    thumb = img.copy()
    thumb.thumbnail(THUMB_SIZE, Image.LANCZOS)
    thumb_key = f"thumbs/{photo_id}.{ext}"
    thumb_buf = io.BytesIO()
    thumb.save(thumb_buf, format=save_format, **save_kwargs)
    thumb_buf.seek(0)
    s3.put_object(Bucket=THUMBS_BUCKET, Key=thumb_key, Body=thumb_buf, ContentType=content_type)

    print(f"Saved: preview={preview_key}  thumb={thumb_key}")

    return {
        "photoId":        photo_id,
        "originalKey":    s3_key,
        "thumbnailKey":   thumb_key,
        "previewKey":     preview_key,
        "photographerId": photographer_id or "",
    }
