#!/usr/bin/env bash
# =============================================================================
# scripts/deploy.sh — Galleria Serverless Deploy Orchestrator
#
# Usage:
#   ./scripts/deploy.sh                   # primary region only (us-east-1)
#   ./scripts/deploy.sh --secondary       # also deploy eu-west-1 after primary
#   ./scripts/deploy.sh --secondary-only  # deploy eu-west-1 (assumes primary done)
#
# Pre-requisites:
#   - AWS CLI v2  (aws --version)
#   - AWS SAM CLI (sam --version)
#   - Python 3.9+ with boto3 (for generate_translations.py)
#   - AWS credentials configured (aws configure or environment variables)
#   - samconfig.toml present in repo root
#
# AWS Cert Note (SAA-C03 / DVA-C02):
#   CloudFormation Outputs are the contract between stacks and automation.
#   This script reads stack Outputs via `aws cloudformation describe-stacks`
#   to get distribution IDs and bucket names — no hardcoded values needed.
#   CloudFront invalidation (/* path) flushes all edge caches so the newly
#   synced index.html / customer.html are served immediately.
# =============================================================================
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
STACK_NAME="${STACK_NAME:-galleria}"
PRIMARY_REGION="${PRIMARY_REGION:-us-east-1}"
SECONDARY_REGION="${SECONDARY_REGION:-eu-west-1}"
SECONDARY_CONFIG_ENV="${SECONDARY_CONFIG_ENV:-euwest1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TRANSLATIONS_DIR="$REPO_ROOT/translations"

# Parse flags
DEPLOY_PRIMARY=true
DEPLOY_SECONDARY=false
for arg in "$@"; do
  case $arg in
    --secondary)       DEPLOY_SECONDARY=true ;;
    --secondary-only)  DEPLOY_PRIMARY=false; DEPLOY_SECONDARY=true ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
log()  { echo "▶  $*"; }
ok()   { echo "✓  $*"; }
warn() { echo "⚠  $*" >&2; }

# Read a named Output value from a CloudFormation stack
cf_output() {
  local region=$1 stack=$2 key=$3
  aws cloudformation describe-stacks \
    --region "$region" \
    --stack-name "$stack" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue" \
    --output text 2>/dev/null || echo ""
}

# Invalidate a CloudFront distribution (/* path)
cf_invalidate() {
  local dist_id=$1
  if [[ -z "$dist_id" ]]; then
    warn "  Skipping CloudFront invalidation — distribution ID empty"
    return
  fi
  log "  Invalidating CloudFront distribution $dist_id …"
  aws cloudfront create-invalidation \
    --distribution-id "$dist_id" \
    --paths "/*" \
    --query "Invalidation.Id" \
    --output text
  ok "  Invalidation queued for $dist_id"
}

# ── Step 0: Build ─────────────────────────────────────────────────────────────
log "SAM build …"
cd "$REPO_ROOT"
sam build --use-container
ok "Build complete"

# ── Step 1: Deploy primary (us-east-1) ───────────────────────────────────────
if $DEPLOY_PRIMARY; then
  log "Deploying primary stack ($PRIMARY_REGION) …"
  sam deploy \
    --config-env default \
    --region "$PRIMARY_REGION" \
    --no-fail-on-empty-changeset

  ok "Primary stack deployed"

  # ── Step 2: Read primary Outputs ─────────────────────────────────────────
  log "Reading primary stack Outputs …"

  FRONTEND_BUCKET=$(cf_output "$PRIMARY_REGION" "$STACK_NAME" "FrontendBucketName")
  FRONTEND_DIST=$(cf_output  "$PRIMARY_REGION" "$STACK_NAME" "FrontendDistributionId")
  PURCHASER_BUCKET=$(cf_output "$PRIMARY_REGION" "$STACK_NAME" "PurchaserBucketName")
  PURCHASER_DIST=$(cf_output  "$PRIMARY_REGION" "$STACK_NAME" "PurchaserDistributionId")
  THUMBS_DIST=$(cf_output     "$PRIMARY_REGION" "$STACK_NAME" "ThumbsDistributionId")
  FRONTEND_URL=$(cf_output    "$PRIMARY_REGION" "$STACK_NAME" "FrontendUrl")
  PURCHASER_URL=$(cf_output   "$PRIMARY_REGION" "$STACK_NAME" "PurchaserUrl")

  # These are used to fill samconfig euwest1 placeholders (printed for reference)
  UP_ARN=$(cf_output        "$PRIMARY_REGION" "$STACK_NAME" "UserPoolArn")
  CUP_ARN=$(cf_output       "$PRIMARY_REGION" "$STACK_NAME" "CustomerUserPoolArn")
  WAF_ARN=$(cf_output       "$PRIMARY_REGION" "$STACK_NAME" "CdnWafWebAclArn")
  BUS_ARN=$(cf_output       "$PRIMARY_REGION" "$STACK_NAME" "GalleriaEventBusArn")

  ok "Primary Outputs:"
  echo "    FrontendBucket:    $FRONTEND_BUCKET"
  echo "    FrontendDist:      $FRONTEND_DIST"
  echo "    PurchaserBucket:   $PURCHASER_BUCKET"
  echo "    PurchaserDist:     $PURCHASER_DIST"
  echo "    ThumbsDist:        $THUMBS_DIST"
  echo "    FrontendURL:       $FRONTEND_URL"
  echo "    PurchaserURL:      $PURCHASER_URL"
  echo ""
  echo "  ── euwest1 samconfig values (copy into samconfig.toml) ──"
  echo "    PrimaryGalleriaUserPoolArn:  $UP_ARN"
  echo "    PrimaryCustomerUserPoolArn:  $CUP_ARN"
  echo "    CdnWafWebAclArn:             $WAF_ARN"
  echo "    PrimaryEventBusArn:          $BUS_ARN"
  echo ""

  # ── Step 3: Generate translations (primary deploy machine has AWS creds) ──
  log "Generating translations …"
  if python3 "$REPO_ROOT/scripts/generate_translations.py" --force; then
    ok "Translations generated"
  else
    warn "generate_translations.py failed — using existing translation files"
  fi

  # ── Step 4: Sync static assets to S3 ─────────────────────────────────────
  if [[ -n "$FRONTEND_BUCKET" ]]; then
    log "Syncing photographer portal to s3://$FRONTEND_BUCKET …"
    aws s3 sync "$REPO_ROOT" "s3://$FRONTEND_BUCKET" \
      --region "$PRIMARY_REGION" \
      --include "index.html" \
      --include "translations/*" \
      --exclude "*" \
      --delete \
      --cache-control "max-age=0,no-cache"
    # Also sync any JS/CSS assets that live alongside index.html
    for ext in js css png ico svg; do
      if ls "$REPO_ROOT"/*."$ext" 2>/dev/null | grep -q .; then
        aws s3 sync "$REPO_ROOT" "s3://$FRONTEND_BUCKET" \
          --region "$PRIMARY_REGION" \
          --include "*.$ext" \
          --exclude "*" \
          --cache-control "max-age=86400"
      fi
    done
    ok "Photographer portal synced"
  else
    warn "FrontendBucketName output empty — skipping s3 sync for photographer portal"
  fi

  if [[ -n "$PURCHASER_BUCKET" ]]; then
    log "Syncing customer gallery to s3://$PURCHASER_BUCKET …"
    aws s3 sync "$REPO_ROOT" "s3://$PURCHASER_BUCKET" \
      --region "$PRIMARY_REGION" \
      --include "customer.html" \
      --include "translations/*" \
      --exclude "*" \
      --delete \
      --cache-control "max-age=0,no-cache"
    for ext in js css png ico svg; do
      if ls "$REPO_ROOT"/*."$ext" 2>/dev/null | grep -q .; then
        aws s3 sync "$REPO_ROOT" "s3://$PURCHASER_BUCKET" \
          --region "$PRIMARY_REGION" \
          --include "*.$ext" \
          --exclude "*" \
          --cache-control "max-age=86400"
      fi
    done
    ok "Customer gallery synced"
  else
    warn "PurchaserBucketName output empty — skipping s3 sync for customer gallery"
  fi

  # ── Step 5: CloudFront invalidations ────────────────────────────────────
  log "Invalidating CloudFront caches (primary) …"
  cf_invalidate "$FRONTEND_DIST"
  cf_invalidate "$PURCHASER_DIST"
  cf_invalidate "$THUMBS_DIST"
  ok "Primary CloudFront invalidations queued"

  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  ✓  Primary deployment complete"
  echo "     Photographer portal : $FRONTEND_URL"
  echo "     Customer gallery    : $PURCHASER_URL"
  echo "════════════════════════════════════════════════════════════"
  echo ""
fi

# ── Step 6: Optional secondary deploy (eu-west-1) ────────────────────────────
if $DEPLOY_SECONDARY; then
  echo ""
  log "Checking samconfig for euwest1 PASTE_PRIMARY_* placeholders …"

  # Warn if placeholders are not yet filled
  if grep -q "PASTE_PRIMARY_" "$REPO_ROOT/samconfig.toml"; then
    warn "samconfig.toml still contains PASTE_PRIMARY_* placeholders!"
    warn "Fill them with the values printed above, then re-run with --secondary-only"
    exit 1
  fi

  log "Deploying secondary stack ($SECONDARY_REGION) …"
  sam deploy \
    --config-env "$SECONDARY_CONFIG_ENV" \
    --region "$SECONDARY_REGION" \
    --no-fail-on-empty-changeset

  ok "Secondary stack deployed"

  # Read secondary Outputs for S3 sync and invalidations
  SEC_FRONTEND_BUCKET=$(cf_output "$SECONDARY_REGION" "$STACK_NAME" "FrontendBucketName")
  SEC_FRONTEND_DIST=$(cf_output   "$SECONDARY_REGION" "$STACK_NAME" "FrontendDistributionId")
  SEC_PURCHASER_BUCKET=$(cf_output "$SECONDARY_REGION" "$STACK_NAME" "PurchaserBucketName")
  SEC_PURCHASER_DIST=$(cf_output  "$SECONDARY_REGION" "$STACK_NAME" "PurchaserDistributionId")
  SEC_THUMBS_DIST=$(cf_output     "$SECONDARY_REGION" "$STACK_NAME" "ThumbsDistributionId")

  # Sync static assets to secondary buckets
  if [[ -n "$SEC_FRONTEND_BUCKET" ]]; then
    log "Syncing photographer portal to s3://$SEC_FRONTEND_BUCKET ($SECONDARY_REGION) …"
    aws s3 sync "$REPO_ROOT" "s3://$SEC_FRONTEND_BUCKET" \
      --region "$SECONDARY_REGION" \
      --include "index.html" --include "translations/*" \
      --exclude "*" --delete \
      --cache-control "max-age=0,no-cache"
    ok "Secondary photographer portal synced"
  fi

  if [[ -n "$SEC_PURCHASER_BUCKET" ]]; then
    log "Syncing customer gallery to s3://$SEC_PURCHASER_BUCKET ($SECONDARY_REGION) …"
    aws s3 sync "$REPO_ROOT" "s3://$SEC_PURCHASER_BUCKET" \
      --region "$SECONDARY_REGION" \
      --include "customer.html" --include "translations/*" \
      --exclude "*" --delete \
      --cache-control "max-age=0,no-cache"
    ok "Secondary customer gallery synced"
  fi

  log "Invalidating CloudFront caches (secondary) …"
  cf_invalidate "$SEC_FRONTEND_DIST"
  cf_invalidate "$SEC_PURCHASER_DIST"
  cf_invalidate "$SEC_THUMBS_DIST"

  SEC_FRONTEND_URL=$(cf_output "$SECONDARY_REGION" "$STACK_NAME" "FrontendUrl")
  SEC_PURCHASER_URL=$(cf_output "$SECONDARY_REGION" "$STACK_NAME" "PurchaserUrl")

  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  ✓  Secondary deployment complete"
  echo "     Photographer portal : $SEC_FRONTEND_URL"
  echo "     Customer gallery    : $SEC_PURCHASER_URL"
  echo "════════════════════════════════════════════════════════════"
fi
