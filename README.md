# 📸 Serverless Photo Galleria

A production-grade, multi-region, event-driven photo marketplace built on AWS using the Serverless Application Model (SAM). The platform connects professional photographers with commercial buyers through two isolated web portals, backed by a fully serverless infrastructure with enterprise-grade security, GDPR compliance, and global scalability.

**Live Demo:**
- 🎨 [Photographer Admin Portal](https://dps7ixpjebpsu.cloudfront.net) — upload, manage, and monetise your photo library
- 🛍️ [Customer Galleria Portal](https://d1zup6wc9h9uwc.cloudfront.net) — browse, licence, and download professional photography

---

## 🏗️ Architecture Overview

The system splits responsibilities into two strictly isolated tiers — the **Administrative Ingestion Tier** (photographers) and the **Public Presentation Tier** (buyers) — connected only through API Gateway with Cognito authentication enforced at every boundary.

```
[ Photographer Portal ]          [ Customer Portal ]
        │                                │
        ▼                                ▼
  Cognito User Pool              Cognito Customer Pool
  (Photographer Auth)            (Buyer Auth — zero lateral access)
        │                                │
        └──────────── API Gateway (WAFv2 protected) ───────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
        Upload URL       List Images      Purchase/Download
              │
              ▼
      S3 Originals Bucket
              │
              ▼
      AWS Step Functions
    ┌────┬────┬────┬────┬──────────┐
    ▼    ▼    ▼    ▼    ▼          ▼
  Blur Crop Resize Rotate Watermark Compress
                    │
                    ▼
           S3 Thumbnails Bucket
                    │
              CloudFront CDN
```

---

## ⚙️ Tech Stack

| Layer | Technology |
|---|---|
| Infrastructure | AWS SAM / CloudFormation |
| Compute | AWS Lambda (Python 3.13) |
| Orchestration | AWS Step Functions (Express Workflows) |
| Database | DynamoDB GlobalTables (multi-region active-active) |
| Storage | Amazon S3 (OAC-protected, no public access) |
| CDN | Amazon CloudFront (4 distributions) |
| Auth | Amazon Cognito (dual user pool isolation) |
| API | Amazon API Gateway (REST, Cognito-authorised) |
| Security | AWS WAFv2 (rate limiting, geo-blocking) |
| Events | Amazon EventBridge (cross-region pub/sub) |
| Payments | Stripe (checkout sessions + webhook verification) |
| Monitoring | CloudWatch Alarms, CloudTrail audit logging |
| Compliance | GDPR Art. 7, 17, 20, 30 (consent, erasure, portability) |

---

## 🛡️ Security Design

**Dual Cognito Pool Isolation** — Photographers and buyers are authenticated through physically separate user pools. A compromised buyer account has zero lateral path into the photographer dashboard.

**Storage Perimeter Hardening** — All S3 buckets block public access entirely. Assets are served exclusively through CloudFront using Origin Access Control (OAC), with pre-signed URLs (300-second TTL) for downloads.

**Least-Privilege IAM** — Every Lambda function has a scoped execution role granting only the specific actions it needs on the specific resources it touches.

**WAFv2 Protection** — API Gateway and CloudFront distributions are protected by WAFv2 Web ACLs with rate limiting rules to block abuse and DDoS attempts.

**Decoupled Data Egress** — The customer portal never queries S3 directly. All asset access is mediated through API Gateway, which generates short-lived signed URLs via Lambda.

---

## 🔒 GDPR Compliance

The platform implements full GDPR compliance for EU users:

- **Art. 7 — Consent**: `/consent` endpoint records explicit opt-in with timestamp and version
- **Art. 17 — Right to Erasure**: `/delete-account` triggers full data deletion across all tables
- **Art. 20 — Data Portability**: `/my-data` exports all personal data as a structured JSON package
- **Art. 30 — Processing Records**: Append-only DynamoDB audit log with 7-year TTL
- **DPA Acceptance**: `/dpa-accept` records Data Processing Agreement acceptance for photographer accounts

---

## 📂 Repository Structure

```
serverless-photo-galleria/
│
├── src/                          # Lambda function handlers (Python 3.13)
│   ├── bedrock_enrich/           # AI-powered photo metadata enrichment (AWS Bedrock)
│   ├── blur/                     # Image blur processing
│   ├── cart/                     # Shopping cart management
│   ├── compress/                 # Image compression
│   ├── consent/                  # GDPR consent recording
│   ├── create_checkout_session/  # Stripe checkout integration
│   ├── crop/                     # Image cropping
│   ├── delete_account/           # GDPR right to erasure
│   ├── delete_photo/             # Photo removal
│   ├── download/                 # Secure asset download
│   ├── dpa_accept/               # Data Processing Agreement acceptance
│   ├── earnings/                 # Photographer revenue analytics
│   ├── embed_photo/              # Photo embed generation
│   ├── get_download/             # Pre-signed download URL generation
│   ├── get_upload_url/           # Pre-signed upload URL generation
│   ├── like/                     # Photo favouriting
│   ├── list_images/              # Photo catalogue listing
│   ├── moderation/               # AI content moderation
│   ├── my_data/                  # GDPR data portability export
│   ├── processing/               # Step Functions pipeline coordinator
│   ├── profile/                  # Photographer profile management
│   ├── refund/                   # Order refund processing
│   ├── resize/                   # Image resizing
│   ├── rotate/                   # Image rotation
│   ├── search/                   # Photo search with pagination
│   ├── stripe_webhook/           # Stripe payment webhook handler
│   ├── tagging/                  # AI-powered photo tagging
│   ├── trigger_pipeline/         # Step Functions pipeline trigger
│   ├── unlike/                   # Photo unfavouriting
│   ├── upload_url/               # Upload URL orchestration
│   └── watermark/                # Photographer watermark application
│
├── statemachine/
│   └── photo_pipeline.asl.json   # Step Functions state machine definition
│
├── index.html                    # Photographer Admin Portal (single-file SPA)
├── customer.html                 # Customer Galleria Portal (single-file SPA)
├── amazon-cognito-identity.min.js # Cognito JS SDK (local, no CDN dependency)
├── template.yaml                 # SAM / CloudFormation infrastructure definition
└── tests/                        # Unit and integration tests
```

---

## 🚀 Deployment

### Prerequisites

- AWS CLI configured with Administrator credentials
- AWS SAM CLI installed (`pip install aws-sam-cli`)
- Python 3.13

### 1. Build and Deploy (Primary Region — us-east-1)

```powershell
sam build
sam deploy --config-env default --guided
```

On first run `--guided` will prompt for parameters and write them to `samconfig.toml` (gitignored). On subsequent runs omit `--guided`.

### 2. Note the Outputs

After deployment completes, copy these values from the Outputs block:

| Output Key | Used For |
|---|---|
| `AdminWebsiteURL` | CloudFront URL for photographer portal |
| `PurchaserWebsiteURL` | CloudFront URL for customer portal |
| `RegionalApiEndpoint` | API Gateway direct URL |
| `UserPoolId` / `UserPoolClientId` | Photographer Cognito config |
| `CustomerUserPoolId` / `CustomerUserPoolClientId` | Customer Cognito config |
| `CdnWafWebAclArn` | Pass to secondary region deploy |
| `UserPoolArn` | Pass to secondary region deploy |
| `GalleriaEventBusArn` | Pass to secondary region deploy |

### 3. Deploy Frontend Files to S3

```powershell
# Photographer Admin Portal
aws s3 cp .\index.html s3://serverless-photo-galleria-frontend-[ACCOUNT-ID]/index.html
aws s3 cp .\amazon-cognito-identity.min.js s3://serverless-photo-galleria-frontend-[ACCOUNT-ID]/amazon-cognito-identity.min.js

# Customer Galleria Portal
aws s3 cp .\customer.html s3://serverless-photo-galleria-purchaser-[ACCOUNT-ID]/customer.html
aws s3 cp .\amazon-cognito-identity.min.js s3://serverless-photo-galleria-purchaser-[ACCOUNT-ID]/amazon-cognito-identity.min.js

# Invalidate CloudFront cache
aws cloudfront create-invalidation --distribution-id [ADMIN-DIST-ID] --paths "/*"
aws cloudfront create-invalidation --distribution-id [PURCHASER-DIST-ID] --paths "/*"
```

---

## 🔄 Core Data Flows

**Photo Upload & Processing**
Photographer logs in → requests pre-signed upload URL → browser pushes master file directly to S3 Originals bucket → S3 event triggers Step Functions → state machine fans out to parallel Lambda workers (blur, crop, resize, rotate, watermark, compress) → processed thumbnails land in private Thumbs bucket → metadata written to DynamoDB.

**Customer Browse & Purchase**
Customer authenticates via isolated Cognito pool → `GET /search` returns paginated, signed thumbnail URLs → customer adds to cart → Stripe checkout session created → payment webhook confirms purchase → order recorded in DynamoDB → `POST /get-download-url` issues 300-second pre-signed URL for full-resolution download.

**Cross-Region Event Propagation**
Purchase and upload events are published to EventBridge custom bus → events replicate to secondary region bus → regional Lambda subscribers process events locally, ensuring consistent state across DynamoDB GlobalTables in both regions.

---

## 📊 DynamoDB Tables (GlobalTables — Active-Active Multi-Region)

| Table | Purpose |
|---|---|
| `PhotoMetadataTable` | Photo catalogue, tags, pricing, photographer ownership |
| `ShoppingCartTable` | Active customer carts |
| `PhotographerProfileTable` | Photographer profiles, watermark config, earnings |
| `OrdersTable` | Completed purchase records |
| `UserConsentTable` | GDPR consent records (Art. 7) |
| `AuditLogTable` | Append-only compliance audit log (Art. 30, 7-year TTL) |

---

## 🌍 Multi-Region Support

The template supports active-active deployment across two regions. The primary region (`us-east-1`) deploys all resources. The secondary region (`eu-west-1`) is deployed by passing outputs from the primary as parameters:

```powershell
sam deploy --config-env eu-west-1 \
  --parameter-overrides \
    PrimaryGalleriaUserPoolArn=[UserPoolArn from primary] \
    PrimaryCustomerUserPoolArn=[CustomerUserPoolArn from primary] \
    PrimaryEventBusArn=[GalleriaEventBusArn from primary] \
    CdnWafWebAclArn=[CdnWafWebAclArn from primary]
```

DynamoDB GlobalTables automatically replicate data between regions with sub-second latency.
