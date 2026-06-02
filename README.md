# serverless-photo-galleria

This project contains source code and supporting files for a serverless application that you can deploy with the SAM CLI. It includes the following files and folders:

📸 Serverless Photo Galleria
A production-grade, event-driven serverless web ecosystem built using the AWS Serverless Application Model (SAM) and optimized with a Python 3.13 backend.

The platform provides an end-to-end commercial pipeline that decouples professional media processing layers from secure customer asset delivery interfaces. It handles image orchestration natively via serverless microservices, ensuring near-zero idle compute overhead and strict logical scaling.

🏗️ Architecture Design & Visual Mapping
The application splits system responsibilities into two strictly isolated horizontal layers: the Administrative Ingestion Tier and the Public Presentation Tier.

                           [ CloudFront / S3 Ingestion ]
                                         │
                                         ▼
                                 Originals Bucket
                                         │
                                         ▼
                              ┌─────────────────────┐
                              │ AWS Step Functions  │
                              └──────────┬──────────┘
                                         │
                 ┌───────────┬───────────┼───────────┬───────────┐
                 ▼           ▼           ▼           ▼           ▼
              ┌──────┐    ┌──────┐    ┌──────┐    ┌──────┐    ┌──────┐
              │ Blur   │    │ Crop   │    │Resize  │    │Rotate│    │Watermark│
              └──┬───┘    └───┬──┘    ───┬───┘    ───┬───┘    ───┬───┘
                 │            │          │           │           │
                 └────────────┴──────────┼───────────┴───────────┘
                                         │
                                         ▼
                                   Thumbs Bucket
                                         │
                ┌────────────────────────┴────────────────────────┐
                ▼                                                 ▼
     ┌──────────────────────┐                          ┌──────────────────────┐
     │  Lambda: ListImages  │                          │   Lambda: Download   │
     └──────────────────────┘                          └──────────────────────┘
                ▲                                                 ▲
                │ (GET /list-images)                              │ (POST /get-download-url)
                └────────────────────────┬────────────────────────┘
                                         │
                                  AWS API Gateway
                                         │
                                         ▼
                            [ Customer Galleria Portal ]
🛡️ Enterprise Security Implementations
Multi-Pool Identity Isolation Strategy: Photographers and commercial buyers are contained within physically separate user directories:

galleria-user-pool (Photographer Authentication Tier)

customer-user-pool (Consumer Egress/Download Tier)
This ensures that a compromised customer profile has zero lateral vector path into the studio dashboard.

Storage Perimeter Hardening: All direct public anonymous read/write traffic is blocked across the S3 storage layers using bucket access policies and CloudFront Origin Access Controls (OAC).

Least-Privilege RBAC: Lambda execution scopes are strictly restricted down to localized resource boundaries using targeted AWS IAM policy blocks.

Decoupled Data Egress (Gatekeeping): The customer portal never queries physical storage. Image indexing is managed exclusively via API Gateway which generates short-lived 300-second pre-signed URLs for asset security.

📂 Repository Structure
.
├── src/                               # Python 3.13 Lambda Core Worker Fleet
│   ├── download/                      # Secure file download pre-signed link engine
│   │   └── app.py
│   ├── blur/                          # Core image mutation engines
│   │   └── app.py
│   ├── compress/                      # ...
│   ├── crop/                          # ...
│   ├── resize/                        # ...
│   ├── rotate/                        # ...
│   └── watermark/                     # Complex binary branding engine
│
├── statemachine/                      # AWS Step Functions Configuration
│   └── photo_pipeline.asl.json        # Declarative State Machine Graph definition
│
├── index.html                         # Photographer Uploader Portal (Admin Panel)
├── customer.html                      # Customer Galleria Portal (Consumer Client)
├── amazon-cognito-identity.min.js     # Cryptographic local client security module
└── template.yaml                      # Unified CloudFormation / SAM Infrastructure Draft
🚀 Deployment & Operational Run-Sheet
Prerequisites
AWS CLI installed and configured with valid IAM Administrator credentials.

AWS SAM CLI installed.

Supported text editor.

1. Backend Cloud Infrastructure Setup
Compile your serverless resources and deploy the centralized CloudFormation stack using the AWS SAM framework:

PowerShell
sam build
sam deploy
Upon successful deployment, copy the dynamic resources from the Outputs block printed in your terminal window:

GalleriaApiEndpoint

UserPoolId & UserPoolClientId

CustomerUserPoolId & CustomerUserPoolClientId

AdminWebsiteURL

PurchaserWebsiteURL

2. Frontend Configuration
Open your local index.html (Admin) and customer.html (Customer) files and update their corresponding CONFIG blocks with your fresh backend values:

JavaScript
const CONFIG = {
    UserPoolId: 'us-east-1_XXXXXXXXX', 
    ClientId: 'XXXXXXXXXXXXXXXXXXXXXXXXXX',
    ApiEndpoint: 'https://XXXXXX.execute-api.us-east-1.amazonaws.com/Prod/process',
    GetUploadUrlEndpoint: '...',
    ListImagesEndpoint: '...'
};
3. Static Web Deployment & CDN Clearing
Push your secure frontend files up to their dedicated S3 bucket silos and force a global CloudFront Content Delivery Network invalidation:

PowerShell
# Deploy Administrative Dashboard Panel
aws s3 cp .\index.html s3://serverless-photo-galleria-frontend-[ACCOUNT-ID]/index.html
aws s3 cp .\amazon-cognito-identity.min.js s3://serverless-photo-galleria-frontend-[ACCOUNT-ID]/amazon-cognito-identity.min.js
aws cloudfront create-invalidation --distribution-id [ADMIN-DISTRIBUTION-ID] --paths "/*"

# Deploy Consumer Presentation Client
aws s3 cp .\customer.html s3://serverless-photo-galleria-purchaser-frontend-[ACCOUNT-ID]/index.html
aws s3 cp .\amazon-cognito-identity.min.js s3://serverless-photo-galleria-purchaser-frontend-[ACCOUNT-ID]/amazon-cognito-identity.min.js
aws cloudfront create-invalidation --distribution-id [CUSTOMER-DISTRIBUTION-ID] --paths "/*"
🔄 Core Data Flow Mechanics
Ingestion Loop: Photographer logs in via index.html ──► Requests pre-signed upload URL ──► Browser pushes raw master file directly to the Originals S3 Bucket.

Orchestration Loop: S3 arrival triggers API Gateway POST /process ──► Hands off tracking pointer to AWS Step Functions ──► State machine routes data layer across parallel Python 3.13 worker Lambdas ──► Finished asset drops securely into private Thumbs S3 Bucket.

Egress Secure Read Loop: Consumer accesses customer.html ──► Authenticates with independent credentials ──► Executes GET /list-images ──► ListImages Lambda queries private bucket, signs keys, and securely maps 300-second view feeds.

Egress Secure Save Loop: Consumer triggers asset download ──► Fires authenticated POST /get-download-url ──► Download Lambda outputs high-resolution time-expired access channel to complete local hardware file transfer.