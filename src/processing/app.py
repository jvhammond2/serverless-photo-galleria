"""
ProcessingFunction — src/processing/app.py
-------------------------------------------
Central image processing engine for the Galleria pipeline.
Invoked by Step Functions after the photographer uploads to OriginalsS3Bucket.

Expected input event:
{
    "s3Bucket": "galleria-originals-...",
    "s3Key":    "my-photo.jpg",
    "adjustments": [
        {"id": "exposure",   "value": 25},
        {"id": "contrast",   "value": -10},
        {"id": "vignette",   "value": 40}
    ]
}

Supported adjustment IDs (all values -100..+100 unless noted):
  exposure      — EV-style brightness (exponential response)
  brilliance    — lift shadows + pull highlights + boost colour (like Apple Photos)
  highlights    — tone curve on the upper luminance range
  shadows       — tone curve on the lower luminance range
  brightness    — linear overall brightness
  contrast      — overall contrast
  blackpoint    — lift shadow floor (0..+100 only)
  saturation    — overall colour saturation
  vibrance      — selective saturation (de-saturated pixels boosted most)
  warmth        — colour temperature (+ warm, - cool)
  tint          — green/magenta tint axis
  sharpness     — unsharp mask strength (0..+100)
  definition    — local contrast / clarity (0..+100)
  noiseReduction— median filter strength (0..+100)
  vignette      — radial darkening vignette (0..+100)

Watermark is always applied last regardless of adjustments.

Outputs:
  PREVIEWS_BUCKET  <- full-size processed image  (previews/<uuid>.jpg)
  THUMBS_BUCKET    <- 300x300 thumbnail           (thumbs/<uuid>.jpg)

Returns to Step Functions:
  { photoId, originalKey, thumbnailKey, previewKey }

AWS Cert Note (SAA-C03 / DVA-C02):
  numpy is bundled via requirements.txt (not pre-installed in Python 3.13).
  For large scale, consider using a Lambda Layer for numpy so the library is
  cached at the execution environment level and cold starts are faster.
"""

import io
import os
import uuid

import boto3
import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

s3       = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

ORIGINALS_BUCKET = os.environ["ORIGINALS_BUCKET"]
THUMBS_BUCKET    = os.environ["THUMBS_BUCKET"]
PREVIEWS_BUCKET  = os.environ["PREVIEWS_BUCKET"]
PROFILE_TABLE    = os.environ.get("PROFILE_TABLE", "")
WATERMARK_TEXT   = os.environ.get("WATERMARK_TEXT", "© Galleria")

THUMB_SIZE = (300, 300)


# ---------------------------------------------------------------------------
# Watermark text helper
# ---------------------------------------------------------------------------

def _get_watermark_text(photographer_id: str | None) -> str:
    if not photographer_id or not PROFILE_TABLE:
        return WATERMARK_TEXT
    try:
        resp   = dynamodb.Table(PROFILE_TABLE).get_item(
            Key={"profileId": photographer_id},
            ProjectionExpression="watermarkText",
        )
        custom = (resp.get("Item", {}).get("watermarkText") or "").strip()
        return custom if custom else WATERMARK_TEXT
    except Exception:
        return WATERMARK_TEXT


# ---------------------------------------------------------------------------
# Utility: value → Pillow enhance factor
#   value is an integer -100..+100 where 0 = no change.
#   Maps to factor range [lo, 1.0, hi] using linear interpolation.
# ---------------------------------------------------------------------------

def _enhance_factor(value: int, lo: float, hi: float) -> float:
    """Map an integer -100..+100 value to a Pillow enhance factor [lo..hi]."""
    v = max(-100, min(100, int(value)))
    if v >= 0:
        return 1.0 + (hi - 1.0) * v / 100.0
    else:
        return 1.0 + (1.0 - lo) * v / 100.0   # v is negative so this subtracts


def _to_arr(img: Image.Image) -> np.ndarray:
    return np.array(img, dtype=np.float32)


def _from_arr(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


# ---------------------------------------------------------------------------
# Effect implementations — each accepts (img, value) and returns img
# ---------------------------------------------------------------------------

def effect_exif_rotate(img: Image.Image, value: int = 0) -> Image.Image:
    """Always-on: correct phone/camera orientation from EXIF."""
    return ImageOps.exif_transpose(img)


def effect_exposure(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Photographic exposure adjustment using an exponential response curve.
    value -100 ≈ -2 stops (very dark), 0 = unchanged, +100 ≈ +2 stops (very bright).

    AWS Cert: This is pure CPU compute — no AWS service calls.
    Lambda billing is per GB-second, so more memory = more CPU = faster = cheaper.
    """
    if value == 0:
        return img
    factor = 2.0 ** (value / 50.0)   # -100→0.25, 0→1.0, +100→4.0
    return ImageEnhance.Brightness(img).enhance(factor)


def effect_brilliance(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Apple-Photos-style brilliance: lifts shadows, reins in highlights, adds
    a gentle colour boost — making the image feel vivid without overexposing.
    """
    if value == 0:
        return img
    img = effect_shadows(img, int(value * 0.45))
    img = effect_highlights(img, -int(value * 0.2))
    img = ImageEnhance.Color(img).enhance(1.0 + (value / 100.0) * 0.3)
    return img


def effect_highlights(img: Image.Image, value: int = 0) -> Image.Image:
    """Tone curve targeting bright pixels (luminance > 0.5)."""
    if value == 0:
        return img
    arr  = _to_arr(img)
    lum  = arr.mean(axis=2) / 255.0          # per-pixel average luminance 0..1
    mask = np.clip((lum - 0.5) * 2, 0, 1)   # 0 for darks, 1 for very bright
    mask = mask[:, :, np.newaxis]
    adj  = (value / 100.0) * 60.0
    return _from_arr(arr + mask * adj)


def effect_shadows(img: Image.Image, value: int = 0) -> Image.Image:
    """Tone curve targeting dark pixels (luminance < 0.5)."""
    if value == 0:
        return img
    arr  = _to_arr(img)
    lum  = arr.mean(axis=2) / 255.0
    mask = np.clip((0.5 - lum) * 2, 0, 1)   # 1 for very dark, 0 for brights
    mask = mask[:, :, np.newaxis]
    adj  = (value / 100.0) * 60.0
    return _from_arr(arr + mask * adj)


def effect_brightness(img: Image.Image, value: int = 0) -> Image.Image:
    """Linear overall brightness."""
    if value == 0:
        return img
    return ImageEnhance.Brightness(img).enhance(_enhance_factor(value, 0.1, 2.0))


def effect_contrast(img: Image.Image, value: int = 0) -> Image.Image:
    """Overall contrast."""
    if value == 0:
        return img
    return ImageEnhance.Contrast(img).enhance(_enhance_factor(value, 0.1, 2.0))


def effect_blackpoint(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Lift the shadow floor — maps pixel 0 to 'lift' and scales the rest.
    value 0 = no change; +100 = heavy lift (faded / matte look).
    """
    if value <= 0:
        return img
    lift = int((value / 100.0) * 50)
    lut  = [min(255, lift + int(i * (255 - lift) / 255)) for i in range(256)]
    return img.point(lut * 3)


def effect_saturation(img: Image.Image, value: int = 0) -> Image.Image:
    """Overall colour saturation. -100 = full greyscale, +100 = hyper-vivid."""
    if value == 0:
        return img
    return ImageEnhance.Color(img).enhance(_enhance_factor(value, 0.0, 2.5))


def effect_vibrance(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Selective saturation: boosts under-saturated pixels more than already-vivid
    ones, protecting skin tones and skies from over-saturation.
    """
    if value == 0:
        return img
    arr    = _to_arr(img)
    mx     = arr.max(axis=2)
    mn     = arr.min(axis=2)
    sat    = np.where(mx > 0, (mx - mn) / mx, 0)   # per-pixel saturation 0..1
    boost  = (1.0 - sat) * (value / 100.0) * 0.9   # low-sat pixels get more boost
    mean   = arr.mean(axis=2, keepdims=True)
    arr    = mean + (arr - mean) * (1.0 + boost[:, :, np.newaxis])
    return _from_arr(arr)


def effect_warmth(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Colour temperature:  positive = warm (amber), negative = cool (blue).
    Implemented as opposing R and B channel shifts.
    """
    if value == 0:
        return img
    r, g, b = img.split()
    shift   = int(abs(value) / 100.0 * 35)
    if value > 0:
        r = r.point(lambda p: min(255, p + shift))
        b = b.point(lambda p: max(0, p - shift))
    else:
        r = r.point(lambda p: max(0, p - shift))
        b = b.point(lambda p: min(255, p + shift))
    return Image.merge("RGB", (r, g, b))


def effect_tint(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Tint axis: positive = green, negative = magenta.
    Adjusts the green channel while leaving R and B untouched.
    """
    if value == 0:
        return img
    r, g, b = img.split()
    shift   = int(abs(value) / 100.0 * 20)
    g = g.point(lambda p: min(255, p + shift) if value > 0 else max(0, p - shift))
    return Image.merge("RGB", (r, g, b))


def effect_sharpness(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Photographic sharpening via unsharp mask with variable radius and strength.
    Low values = gentle edge crispening; high values = aggressive fine-detail boost.
    """
    if value <= 0:
        return img
    radius  = 1.5 + (value / 100.0) * 1.5   # 1.5 → 3.0
    percent = 80 + int(value * 2.2)           # 80 → 300
    return img.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=3))


def effect_definition(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Definition / Clarity: large-radius unsharp mask that boosts mid-tone contrast,
    making textures and fine detail pop without affecting overall brightness.
    Equivalent to the 'Clarity' slider in Lightroom.
    """
    if value <= 0:
        return img
    percent = int(value * 1.8)   # up to 180 %
    return img.filter(ImageFilter.UnsharpMask(radius=20, percent=percent, threshold=0))


def effect_noise_reduction(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Median filter noise reduction.  Low values use a 3x3 kernel (gentle);
    high values use 5x5 (stronger, may soften fine detail).
    """
    if value <= 0:
        return img
    size = 5 if value >= 50 else 3
    return img.filter(ImageFilter.MedianFilter(size=size))


def effect_vignette(img: Image.Image, value: int = 0) -> Image.Image:
    """
    Radial darkening vignette -- darkens corners, leaving the centre bright.
    Implemented as a numpy radial gradient multiplied into each channel.
    """
    if value <= 0:
        return img
    w, h     = img.size
    arr      = _to_arr(img)
    cy, cx   = h / 2.0, w / 2.0
    Y, X     = np.ogrid[:h, :w]
    dist     = np.sqrt(((X - cx) / cx) ** 2 + ((Y - cy) / cy) ** 2)
    strength = (value / 100.0) * 0.85
    mask     = np.clip(1.0 - dist * strength, 0, 1)[:, :, np.newaxis]
    return _from_arr(arr * mask)


def effect_watermark(img: Image.Image, watermark_text: str = "") -> Image.Image:
    """Semi-transparent text watermark in the bottom-right corner."""
    text      = watermark_text or WATERMARK_TEXT
    w, h      = img.size
    font_size = max(24, min(w, h) // 22)
    font      = ImageFont.load_default(size=font_size)

    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox  = dummy.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]

    base    = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)
    margin  = max(16, font_size // 2)
    x, y   = w - tw - margin, h - th - margin
    draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 140))
    draw.text((x, y),         text, font=font, fill=(255, 255, 255, 210))
    return Image.alpha_composite(base, overlay).convert("RGB")


# ---------------------------------------------------------------------------
# Dispatch table  (adjustment id -> function)
# ---------------------------------------------------------------------------

ADJUSTMENT_FNS = {
    "exposure":        effect_exposure,
    "brilliance":      effect_brilliance,
    "highlights":      effect_highlights,
    "shadows":         effect_shadows,
    "brightness":      effect_brightness,
    "contrast":        effect_contrast,
    "blackpoint":      effect_blackpoint,
    "saturation":      effect_saturation,
    "vibrance":        effect_vibrance,
    "warmth":          effect_warmth,
    "tint":            effect_tint,
    "sharpness":       effect_sharpness,
    "definition":      effect_definition,
    "noiseReduction":  effect_noise_reduction,
    "vignette":        effect_vignette,
}

# Sharpening-family adjustments are applied last (before watermark) so they
# don't interact with luminance/colour operations.
SHARPEN_IDS = {"sharpness", "definition"}


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handler(event, context):
    s3_bucket       = event["s3Bucket"]
    s3_key          = event["s3Key"]
    adjustments     = event.get("adjustments", [])   # [{id, value}, ...]
    photographer_id = event.get("photographerId")
    photo_id        = str(uuid.uuid4())

    watermark_text = _get_watermark_text(photographer_id)

    print(f"Processing photoId={photo_id}  key={s3_key}  adjustments={adjustments}")

    # 1. Download original from S3
    obj        = s3.get_object(Bucket=s3_bucket, Key=s3_key)
    image_data = obj["Body"].read()
    with Image.open(io.BytesIO(image_data)) as src:
        img = src.copy()

    if img.mode not in ("RGB",):
        img = img.convert("RGB")

    # Always correct EXIF rotation first
    img = effect_exif_rotate(img)

    # Split adjustments: colour/tone first, sharpening last
    colour_adjs  = [a for a in adjustments if a.get("id") not in SHARPEN_IDS]
    sharpen_adjs = [a for a in adjustments if a.get("id") in SHARPEN_IDS]

    for adj in colour_adjs + sharpen_adjs:
        adj_id = adj.get("id", "")
        value  = int(adj.get("value", 0))
        fn     = ADJUSTMENT_FNS.get(adj_id)
        if fn:
            img = fn(img, value)
            print(f"  Applied: {adj_id}={value}")
        else:
            print(f"  Skipped unknown adjustment: {adj_id}")

    # Watermark always applied last
    img = effect_watermark(img, watermark_text)

    # 2. Save as JPEG
    save_kwargs  = {"quality": 88, "optimize": True}
    content_type = "image/jpeg"
    ext          = "jpg"

    # 3. Full-size preview -> PREVIEWS_BUCKET
    preview_key = f"previews/{photo_id}.{ext}"
    preview_buf = io.BytesIO()
    img.save(preview_buf, format="JPEG", **save_kwargs)
    preview_buf.seek(0)
    s3.put_object(Bucket=PREVIEWS_BUCKET, Key=preview_key,
                  Body=preview_buf, ContentType=content_type)

    # 4. Thumbnail -> THUMBS_BUCKET (aspect-preserving, max 300x300)
    thumb = img.copy()
    thumb.thumbnail(THUMB_SIZE, Image.LANCZOS)
    thumb_key = f"thumbs/{photo_id}.{ext}"
    thumb_buf = io.BytesIO()
    thumb.save(thumb_buf, format="JPEG", **save_kwargs)
    thumb_buf.seek(0)
    s3.put_object(Bucket=THUMBS_BUCKET, Key=thumb_key,
                  Body=thumb_buf, ContentType=content_type)

    print(f"Saved: preview={preview_key}  thumb={thumb_key}")

    return {
        "photoId":        photo_id,
        "originalKey":    s3_key,
        "thumbnailKey":   thumb_key,
        "previewKey":     preview_key,
        "photographerId": photographer_id or "",
    }
