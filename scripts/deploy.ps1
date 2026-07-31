# =============================================================================
# scripts\deploy.ps1 — Galleria One-Command Deploy (Windows PowerShell)
#
# Usage (from project root):
#   .\scripts\deploy.ps1                  # primary region only (us-east-1)
#   .\scripts\deploy.ps1 -Secondary       # primary + eu-west-1
#   .\scripts\deploy.ps1 -SecondaryOnly   # eu-west-1 only
#
# Pre-requisites:
#   - AWS CLI installed and configured (aws configure)
#   - AWS SAM CLI installed (sam --version)
#   - Run from the project root directory
# =============================================================================

param(
    [switch]$Secondary,
    [switch]$SecondaryOnly
)

$ErrorActionPreference = "Stop"

$StackName      = "serverless-photo-galleria"
$PrimaryRegion  = "us-east-1"
$SecondaryRegion = "eu-west-1"

function Log($msg)  { Write-Host "▶  $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "✓  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "⚠  $msg" -ForegroundColor Yellow }
function Fail($msg) { Write-Host "✗  $msg" -ForegroundColor Red; exit 1 }

function Get-CfOutput($region, $key) {
    $val = aws cloudformation describe-stacks `
        --region $region `
        --stack-name $StackName `
        --query "Stacks[0].Outputs[?OutputKey=='$key'].OutputValue" `
        --output text 2>$null
    return $val
}

function Invalidate($distId) {
    if (-not $distId) { Warn "  Skipping invalidation — no distribution ID"; return }
    Log "  Invalidating $distId ..."
    aws cloudfront create-invalidation --distribution-id $distId --paths "/*" --query "Invalidation.Id" --output text | Out-Null
    Ok "  Invalidation queued for $distId"
}

# ── Step 0: Build ─────────────────────────────────────────────────────────────
Log "SAM build ..."
sam build
Ok "Build complete"

# ── Step 1: Deploy primary ────────────────────────────────────────────────────
if (-not $SecondaryOnly) {
    Log "Deploying primary stack ($PrimaryRegion) ..."
    sam deploy --config-env default --region $PrimaryRegion --no-fail-on-empty-changeset
    Ok "Primary stack deployed"

    Log "Reading primary stack outputs ..."
    $FrontendBucket   = Get-CfOutput $PrimaryRegion "FrontendBucketName"
    $FrontendDist     = Get-CfOutput $PrimaryRegion "FrontendDistributionId"
    $PurchaserBucket  = Get-CfOutput $PrimaryRegion "PurchaserBucketName"
    $PurchaserDist    = Get-CfOutput $PrimaryRegion "PurchaserDistributionId"
    $FrontendUrl      = Get-CfOutput $PrimaryRegion "FrontendUrl"
    $PurchaserUrl     = Get-CfOutput $PrimaryRegion "PurchaserUrl"

    # ── Step 2: Upload static files ───────────────────────────────────────────
    Log "Uploading index.html to $FrontendBucket ..."
    aws s3 cp index.html "s3://$FrontendBucket/index.html" --content-type "text/html" --cache-control "no-cache"

    Log "Uploading customer.html to $PurchaserBucket ..."
    aws s3 cp customer.html "s3://$PurchaserBucket/customer.html" --content-type "text/html" --cache-control "no-cache"

    Log "Uploading i18n.js ..."
    aws s3 cp i18n.js "s3://$FrontendBucket/i18n.js" --content-type "application/javascript"
    aws s3 cp i18n.js "s3://$PurchaserBucket/i18n.js" --content-type "application/javascript"

    Log "Syncing translations ..."
    aws s3 sync translations/ "s3://$FrontendBucket/translations/"
    aws s3 sync translations/ "s3://$PurchaserBucket/translations/"

    Ok "Static files uploaded"

    # ── Step 3: Invalidate CloudFront ─────────────────────────────────────────
    Log "Invalidating CloudFront caches (primary) ..."
    Invalidate $FrontendDist
    Invalidate $PurchaserDist

    Write-Host ""
    Write-Host "════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  ✓  Primary deployment complete" -ForegroundColor Green
    Write-Host "     Photographer : $FrontendUrl" -ForegroundColor Green
    Write-Host "     Customer     : $PurchaserUrl" -ForegroundColor Green
    Write-Host "════════════════════════════════════════" -ForegroundColor Green
    Write-Host ""
}

# ── Step 4: Optional secondary deploy ─────────────────────────────────────────
if ($Secondary -or $SecondaryOnly) {
    Log "Deploying secondary stack ($SecondaryRegion) ..."
    sam deploy --config-env euwest1 --region $SecondaryRegion --no-fail-on-empty-changeset
    Ok "Secondary stack deployed"

    $SecFrontendBucket  = Get-CfOutput $SecondaryRegion "FrontendBucketName"
    $SecFrontendDist    = Get-CfOutput $SecondaryRegion "FrontendDistributionId"
    $SecPurchaserBucket = Get-CfOutput $SecondaryRegion "PurchaserBucketName"
    $SecPurchaserDist   = Get-CfOutput $SecondaryRegion "PurchaserDistributionId"
    $SecFrontendUrl     = Get-CfOutput $SecondaryRegion "FrontendUrl"
    $SecPurchaserUrl    = Get-CfOutput $SecondaryRegion "PurchaserUrl"

    Log "Uploading static files to secondary region ..."
    aws s3 cp index.html "s3://$SecFrontendBucket/index.html" --content-type "text/html" --cache-control "no-cache" --region $SecondaryRegion
    aws s3 cp customer.html "s3://$SecPurchaserBucket/customer.html" --content-type "text/html" --cache-control "no-cache" --region $SecondaryRegion
    aws s3 cp i18n.js "s3://$SecFrontendBucket/i18n.js" --content-type "application/javascript" --region $SecondaryRegion
    aws s3 cp i18n.js "s3://$SecPurchaserBucket/i18n.js" --content-type "application/javascript" --region $SecondaryRegion
    aws s3 sync translations/ "s3://$SecFrontendBucket/translations/" --region $SecondaryRegion
    aws s3 sync translations/ "s3://$SecPurchaserBucket/translations/" --region $SecondaryRegion
    Ok "Secondary static files uploaded"

    Log "Invalidating CloudFront caches (secondary) ..."
    Invalidate $SecFrontendDist
    Invalidate $SecPurchaserDist

    Write-Host ""
    Write-Host "════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  ✓  Secondary deployment complete" -ForegroundColor Green
    Write-Host "     Photographer : $SecFrontendUrl" -ForegroundColor Green
    Write-Host "     Customer     : $SecPurchaserUrl" -ForegroundColor Green
    Write-Host "════════════════════════════════════════" -ForegroundColor Green
}
