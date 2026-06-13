# Resume Content — Serverless Photo Galleria Project

---

## Project Title (for resume)

**Galleria — Serverless Fine Art Photography Marketplace**
*Full-stack, multi-region, event-driven cloud application | Personal Project*

---

## One-Line Summary

Designed and built a production-grade, multi-region serverless photography marketplace on AWS, integrating AI/ML, payment processing, GDPR compliance, and real-time image processing pipelines entirely without traditional servers.

---

## Project Bullet Points (pick the ones most relevant to the role)

### Architecture & Cloud Infrastructure
- Architected a fully serverless, event-driven application on AWS using SAM/CloudFormation with 37 Lambda functions, Step Functions Express Workflows, and DynamoDB GlobalTables active-active replication across us-east-1 and eu-west-1
- Designed a multi-tier security model with dual Cognito user pool isolation, WAFv2 rate limiting, Origin Access Control (OAC) on all S3 buckets, and least-privilege IAM roles scoped per Lambda function
- Implemented 4 CloudFront distributions serving assets via pre-signed URLs (300-second TTL) with zero direct S3 public access
- Configured EventBridge for cross-region pub/sub event routing between primary and secondary regions

### AI / Machine Learning
- Integrated Amazon Bedrock (Claude Haiku) to generate real-time AI composition feedback on uploaded photos, cached in DynamoDB to avoid redundant inference calls
- Built a semantic search pipeline using Amazon Titan Embeddings with cosine similarity re-ranking for natural-language photo discovery
- Implemented perceptual hashing (pHash via imagehash library) for near-duplicate image fingerprinting and a similarity search endpoint
- Used Amazon Rekognition for automated content moderation and AI-generated photo tag extraction

### Image Processing Pipeline
- Built a Step Functions–orchestrated image processing pipeline: blur → crop → resize → rotate → watermark → compress, triggered by S3 PUT events
- Implemented dominant colour palette extraction using Pillow's median-cut quantization algorithm with HSV-based mood classification (warm, cool, neutral, dark, etc.)
- Extracted and embedded GPS EXIF metadata using Pillow and piexif; displayed on an interactive Leaflet.js map in the customer portal
- Applied programmatic EXIF copyright watermarking and injected photographer metadata into image files at ingest time

### Payments & E-Commerce
- Integrated Stripe Checkout Sessions and webhook verification for secure purchase flows
- Implemented DynamoDB idempotent webhook writes using `attribute_not_exists` condition expressions to prevent duplicate order records
- Built a collector portfolio feature recording every purchase to a composite-key DynamoDB table (buyerId PK + photoId SK) for instant My Collection queries

### GDPR & Compliance
- Implemented full GDPR compliance: Art. 7 (consent recording), Art. 17 (right to erasure), Art. 20 (data portability JSON export), Art. 30 (append-only audit log with 7-year TTL)
- Built DPA acceptance endpoint and GDPR-compliant account deletion cascade across all DynamoDB tables

### Audio & Media
- Built a voice story feature using the browser MediaRecorder API (audio/webm) with Lambda-generated S3 presigned PUT URLs for upload and presigned GET URLs for streaming playback in the lightbox

### Internationalisation
- Generated translations for 49 languages via AWS Translate using a Python script with batch processing and a language manifest; integrated a runtime language switcher in both portals

### Frontend & UX
- Built two single-page applications (index.html — photographer portal; customer.html — customer portal) with no frontend framework, using vanilla JavaScript, Cognito JWT authentication, and dynamic UI features including: ambient lightbox colour shifting, print wall mockup, drag-and-drop upload with effect presets, and a follow/feed system
- Implemented series/essay mode allowing photographers to group photos into ordered narratives with a sequential viewer

### Observability & DevOps
- Instrumented every Lambda with AWS X-Ray annotations and metadata for per-photo, per-photographer trace filtering
- Configured CloudWatch Alarms and CloudTrail audit logging across both regions
- Managed full infrastructure lifecycle via SAM CLI: build, deploy (multi-region), and teardown (`sam delete`)
- Used Git and GitHub for version control; resolved index lock conflicts, managed credential authentication via Windows Credential Manager and GitHub PATs

---

## Skills / Technologies to List on Resume

**Cloud:** AWS Lambda, AWS SAM, CloudFormation, Step Functions, DynamoDB (GlobalTables, GSI, BatchGetItem), S3, CloudFront, API Gateway, Cognito, WAFv2, EventBridge, Rekognition, Bedrock, Translate, X-Ray, CloudWatch, CloudTrail

**Languages:** Python 3.13, JavaScript (ES2020), PowerShell

**Libraries / Tools:** Pillow, imagehash, piexif, Stripe SDK, Leaflet.js, MediaRecorder API, AWS SAM CLI, Git

**Concepts:** Serverless architecture, event-driven design, multi-region active-active, least-privilege IAM, GDPR compliance, semantic search, perceptual hashing, presigned URLs, JWT authentication, CI/CD with SAM

---

## Certifications in Progress (based on project alignment)

- AWS Certified Solutions Architect – Associate (SAA-C03)
- AWS Certified Developer – Associate (DVA-C02)
