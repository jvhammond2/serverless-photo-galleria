#!/usr/bin/env python3
"""
generate_translations.py
========================
Reads translations/en.json (the English master) and uses AWS Translate to
produce a translations/{lang}.json file for each of the 50 supported target
languages.  Run this script once after any string change, then upload the
translations/ folder to S3 alongside the HTML files.

AWS Certification Note (SAA-C03 / DVA-C02):
  - We use TranslateText (synchronous) rather than StartTextTranslationJob
    (async batch) because our payload is small (< few KB per language).
  - Translate cost = $15 per million characters translated.
    With ~3,000 characters of source text × 49 languages ≈ 147,000 chars
    = ~$0.002 total.  Negligible, and we only pay once per string change.
  - The script skips a language file if it already exists (--force to
    regenerate all).

Usage:
  # one-time setup
  pip install boto3 --break-system-packages

  # generate missing language files (idempotent)
  python scripts/generate_translations.py

  # force regenerate everything
  python scripts/generate_translations.py --force

  # upload to S3 after generation
  aws s3 sync translations/ s3://YOUR-STATIC-BUCKET/translations/ \
      --cache-control "max-age=86400" --content-type "application/json"

Prerequisites:
  AWS credentials configured (aws configure, or IAM role on the build server).
  Required IAM permission: translate:TranslateText
"""

import argparse
import copy
import json
import logging
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TRANSLATIONS_DIR = Path(__file__).parent.parent / "translations"
SOURCE_FILE = TRANSLATIONS_DIR / "en.json"
SOURCE_LANG = "en"

# Top-50 world languages supported by AWS Translate.
# Codes follow the AWS Translate language code convention.
# RTL languages are marked so the frontend can apply dir="rtl".
TARGET_LANGUAGES = [
    # Code       Display name              RTL?
    ("zh",       "中文 (简体)",              False),
    ("zh-TW",    "中文 (繁體)",              False),
    ("hi",       "हिन्दी",                    False),
    ("es",       "Español",                 False),
    ("fr",       "Français",               False),
    ("ar",       "العربية",                  True),
    ("bn",       "বাংলা",                    False),
    ("ru",       "Русский",                False),
    ("pt",       "Português",              False),
    ("ur",       "اردو",                    True),
    ("id",       "Bahasa Indonesia",        False),
    ("de",       "Deutsch",                False),
    ("ja",       "日本語",                   False),
    ("tr",       "Türkçe",                  False),
    ("vi",       "Tiếng Việt",             False),
    ("ko",       "한국어",                   False),
    ("fa",       "فارسی",                   True),
    ("it",       "Italiano",               False),
    ("th",       "ภาษาไทย",                 False),
    ("nl",       "Nederlands",             False),
    ("pl",       "Polski",                 False),
    ("uk",       "Українська",             False),
    ("ro",       "Română",                 False),
    ("ms",       "Bahasa Melayu",           False),
    ("sw",       "Kiswahili",              False),
    ("tl",       "Filipino",               False),
    ("ta",       "தமிழ்",                   False),
    ("te",       "తెలుగు",                  False),
    ("mr",       "मराठी",                   False),
    ("gu",       "ગુજરાતી",                 False),
    ("kn",       "ಕನ್ನಡ",                   False),
    ("ml",       "മലയാളം",                  False),
    ("pa",       "ਪੰਜਾਬੀ",                  False),
    ("am",       "አማርኛ",                   False),
    ("si",       "සිංහල",                   False),
    ("mn",       "Монгол",                 False),
    ("uz",       "Oʻzbek",                 False),
    ("kk",       "Қазақ",                  False),
    ("az",       "Azərbaycan",             False),
    ("he",       "עברית",                   True),
    ("el",       "Ελληνικά",               False),
    ("sv",       "Svenska",                False),
    ("da",       "Dansk",                  False),
    ("no",       "Norsk",                  False),
    ("fi",       "Suomi",                  False),
    ("hu",       "Magyar",                 False),
    ("cs",       "Čeština",                False),
    ("sk",       "Slovenčina",             False),
    ("hr",       "Hrvatski",               False),
]

# AWS Translate hard limit: 10,000 bytes per call.
# We translate one string at a time to stay well within this limit and
# to handle errors per-string without losing the whole batch.
MAX_BYTES_PER_CALL = 9000  # conservative buffer below the 10,000 limit

# Delay between API calls to avoid ThrottlingException (default quota:
# 100 calls/second for TranslateText).  0.02 s ≈ 50 calls/s — safe margin.
CALL_DELAY_SECONDS = 0.02

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


def flatten(obj: dict, prefix: str = "") -> dict[str, str]:
    """Recursively flatten a nested dict to dot-notation keys.

    Example:
        {"auth": {"welcome": "Welcome"}} → {"auth.welcome": "Welcome"}
    """
    items: dict[str, str] = {}
    for k, v in obj.items():
        full_key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            items.update(flatten(v, full_key))
        else:
            items[full_key] = str(v)
    return items


def unflatten(flat: dict[str, str]) -> dict:
    """Rebuild a nested dict from dot-notation keys."""
    result: dict = {}
    for key, value in flat.items():
        parts = key.split(".")
        d = result
        for part in parts[:-1]:
            d = d.setdefault(part, {})
        d[parts[-1]] = value
    return result


def translate_string(client, text: str, target_lang: str) -> str:
    """Translate a single string, returning the original on any error."""
    if not text.strip():
        return text
    if len(text.encode("utf-8")) > MAX_BYTES_PER_CALL:
        log.warning("String too long to translate, returning original: %s…", text[:60])
        return text
    try:
        resp = client.translate_text(
            Text=text,
            SourceLanguageCode=SOURCE_LANG,
            TargetLanguageCode=target_lang,
        )
        return resp["TranslatedText"]
    except (BotoCoreError, ClientError) as exc:
        log.warning("Translate error for lang=%s text=%r: %s", target_lang, text[:40], exc)
        return text


def translate_language(
    client,
    flat_strings: dict[str, str],
    lang_code: str,
    lang_name: str,
    is_rtl: bool,
    output_path: Path,
    force: bool,
) -> None:
    """Translate all strings to one target language and write the JSON file."""
    if output_path.exists() and not force:
        log.info("  Skipping %s (%s) — file exists. Use --force to regenerate.", lang_code, lang_name)
        return

    log.info("  Translating → %s (%s)…", lang_name, lang_code)
    translated_flat: dict[str, str] = {}
    total = len(flat_strings)

    for idx, (key, value) in enumerate(flat_strings.items(), start=1):
        translated_value = translate_string(client, value, lang_code)
        translated_flat[key] = translated_value
        time.sleep(CALL_DELAY_SECONDS)

        if idx % 20 == 0:
            log.info("    %d / %d strings done", idx, total)

    # Rebuild nested structure and add RTL metadata
    nested = unflatten(translated_flat)
    nested["_meta"] = {
        "lang": lang_code,
        "name": lang_name,
        "rtl": is_rtl,
        "generated_from": "en",
    }

    output_path.write_text(
        json.dumps(nested, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("    ✓ Written: %s", output_path)


def write_language_manifest(target_languages: list) -> None:
    """Write translations/languages.json — the language picker data.

    The frontend loads this once to populate the language switcher dropdown.
    """
    manifest = [
        {"code": code, "name": name, "rtl": rtl}
        for code, name, rtl in target_languages
    ]
    # English is always first
    manifest.insert(0, {"code": "en", "name": "English", "rtl": False})

    manifest_path = TRANSLATIONS_DIR / "languages.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("Wrote language manifest → %s", manifest_path)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Galleria translations via AWS Translate")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-generate language files even if they already exist.",
    )
    parser.add_argument(
        "--lang",
        metavar="CODE",
        help="Translate only this language code (e.g. ja, fr). Default: all.",
    )
    parser.add_argument(
        "--region",
        default="us-east-1",
        help="AWS region for the Translate endpoint (default: us-east-1).",
    )
    args = parser.parse_args()

    if not SOURCE_FILE.exists():
        log.error("Master file not found: %s", SOURCE_FILE)
        sys.exit(1)

    TRANSLATIONS_DIR.mkdir(parents=True, exist_ok=True)

    source_data: dict = json.loads(SOURCE_FILE.read_text(encoding="utf-8"))
    flat_strings: dict[str, str] = flatten(source_data)
    log.info("Loaded %d strings from %s", len(flat_strings), SOURCE_FILE)

    # Filter to requested language if --lang was given
    languages_to_process = TARGET_LANGUAGES
    if args.lang:
        languages_to_process = [(c, n, r) for c, n, r in TARGET_LANGUAGES if c == args.lang]
        if not languages_to_process:
            log.error("Unknown language code: %s", args.lang)
            sys.exit(1)

    client = boto3.client("translate", region_name=args.region)

    log.info("Starting translation for %d language(s)…", len(languages_to_process))
    for lang_code, lang_name, is_rtl in languages_to_process:
        output_path = TRANSLATIONS_DIR / f"{lang_code}.json"
        translate_language(
            client=client,
            flat_strings=flat_strings,
            lang_code=lang_code,
            lang_name=lang_name,
            is_rtl=is_rtl,
            output_path=output_path,
            force=args.force,
        )

    write_language_manifest(TARGET_LANGUAGES)
    log.info("Done.")


if __name__ == "__main__":
    main()
