# How I Built a Production-Grade Serverless Photography Marketplace on AWS — Part 1: Architecture

*This is the first in a series of articles about building Galleria, a serverless fine art photography marketplace I built on AWS. Each article covers one layer of the stack.*

I built Galleria as a fully serverless photography marketplace on AWS. It connects professional photographers with collectors and commercial buyers, and every piece of it runs on managed, event-driven AWS services. No servers to patch, no idle compute costs, and no infrastructure to babysit at 2 a.m. This article covers how it is structured and why I made the choices I did.

---

## What Is Galleria?

Galleria is a fine art photography marketplace with two completely separate portals. The Photographer Portal is where photographers upload, manage, and monetize their work. The Customer Portal is where buyers browse, search, and purchase photos.

These are not just two pages of the same app. They are two isolated tiers with separate authentication systems, separate access controls, and zero lateral connection between them. A buyer's account cannot touch anything on the photographer side, and that is by design.

---

## Why Serverless?

Three reasons drove this decision.

**Cost at low traffic.** A traditional server sits idle and runs up a bill regardless of whether anyone is using the app. Lambda charges only for actual execution time, billed in milliseconds. For a marketplace that is not yet running thousands of requests per hour, the difference is significant.

**Operational simplicity.** AWS manages patching, scaling, and availability for every service in this stack. That frees up all available engineering time for features rather than infrastructure maintenance.

**AWS certification alignment.** I am actively studying for the AWS Solutions Architect Associate (SAA-C03) and Solutions Architect Professional (SAP-C02) certifications. Building real infrastructure with the services on those exams is a far better study method than flashcards.

---

## The Two-Tier Isolation Model

The most important architectural decision in Galleria is the strict separation between the photographer and customer tiers.

```
[ Photographer Portal ]          [ Customer Portal ]
        │                                │
        ▼                                ▼
  Cognito User Pool              Cognito Customer Pool
  (Photographer Auth)            (Buyer Auth)
        │                                │
        └──────────── API Gateway ───────┘
                    (WAFv2 protected)
```

Each portal authenticates through a physically separate Amazon Cognito user pool. This means a compromised buyer account has no path into the photographer dashboard, photographers cannot access buyer order data, and each pool has its own client IDs, scopes, and JWT tokens.

I call this the dual pool isolation pattern. I see it referenced in AWS security documentation but rarely implemented fully in personal projects. It adds setup complexity upfront and pays for itself the moment you start thinking about what happens when credentials are stolen.

Both pools connect to the same API Gateway, which sits behind a WAFv2 Web ACL with rate limiting rules. Every API endpoint validates the Cognito JWT before the Lambda function even receives the request.

---

## The Core Infrastructure Components

### Compute: AWS Lambda (Python 3.13)

The entire backend runs on Lambda. There are 37 functions in total, each scoped to a single responsibility. One for upload URL generation, one for search, one for payments, one for profile management, and so on.

Every function has its own IAM execution role granting only the specific actions it needs on the specific resources it touches, not a shared admin role and not a wildcard policy. This is least-privilege IAM in practice, and it is one of the key principles tested on the SAA-C03 exam.

### Database: DynamoDB GlobalTables

All application data lives in DynamoDB, configured as GlobalTables with active-active replication between us-east-1 (primary) and eu-west-1 (secondary). Writes in either region automatically replicate to the other with sub-second latency.

The tables include:

| Table | Purpose |
|---|---|
| PhotoMetadataTable | Photo catalogue, tags, pricing, pHash, GPS |
| ProfileTable | Photographer profile, bio, equipment, watermark text |
| CartTable | Buyer shopping cart (active and pending items) |
| OrdersTable | Completed purchase records |
| CollectionsTable | Buyer collections (buyerId + photoId composite key) |
| FollowsTable | Photographer follow relationships |
| SeriesTable | Photo essay series with GSI on photographerId |
| UserConsentTable | GDPR consent records |
| AuditLogTable | Append-only compliance audit log (7-year TTL) |

DynamoDB's pricing model, pay per read/write capacity consumed, fits the serverless cost philosophy well.

### Storage: Amazon S3 (Zero Public Access)

Photo assets live in three dedicated S3 buckets: one for originals (the raw upload), one for processed thumbnails, and one for full-size previews served to browsing customers. A fourth bucket stores photographer audio notes. Two additional buckets host the static frontend HTML and JavaScript for each portal. All six have public access blocked entirely.

All asset access is mediated through CloudFront using Origin Access Control (OAC), which is the current recommended replacement for the older Origin Access Identity (OAI) pattern. Photographers get pre-signed PUT URLs to upload directly to S3 without routing bytes through Lambda. Buyers get short-lived pre-signed GET URLs for downloads, signed by the Lambda's IAM role, scoped to a single object, and expired after a short window so a leaked URL becomes useless quickly.

### CDN: Amazon CloudFront

Four CloudFront distributions serve the application. One for the Photographer Portal SPA, one for the Customer Portal SPA, one for photo thumbnails and previews, and one for the API endpoint with WAFv2 protection.

The API distribution sits behind a WAFv2 Web ACL with rate limiting and managed rule groups. The portal distributions serve the frontend files directly from S3 with no web servers or containers involved.

### Orchestration: AWS Step Functions

When a photographer uploads a photo, or reprocesses one using the in-app adjustment tools, a Lambda function starts a Step Functions Standard Workflow that runs the photo through six stages.

```
Upload / Adjustment Submit
        │
        ▼
  TriggerPipeline Lambda
        │
        ▼
  Step Functions — PhotoPipelineStateMachine
  ├── ModerateContent   → Rekognition scans for explicit content
  │                       (quarantines photo and stops pipeline if flagged)
  ├── ProcessImage      → Pillow generates thumbnail + preview,
  │                       applies photographer adjustments
  │                       (14 parameters: exposure, highlights, shadows,
  │                       brightness, contrast, saturation, vibrance,
  │                       warmth, tint, sharpness, definition, and more),
  │                       burns watermark, embeds EXIF copyright metadata
  ├── TagImage          → Extracts GPS/EXIF, computes perceptual hash (pHash),
  │                       identifies dominant color palette, calls Rekognition
  │                       for scene and object labels
  ├── EnrichWithAI      → Bedrock (Claude) generates title, description,
  │                       and keyword tags from the image
  ├── EmbedPhoto        → Titan Embeddings generates a vector for
  │                       semantic similarity search
  └── NotifyProcessed   → EventBridge publishes photo.processed event
                          (or photo.pipeline.failed on any stage error)
```

The adjustment panel in the Photographer Portal covers 14 parameters across exposure, tone, color, and detail. When a photographer applies edits and clicks Process, those values are passed as input to Step Functions and consumed by the ProcessImage stage. The same pipeline that handles a fresh upload also handles a reprocess request, with the adjustment parameters riding along as pipeline state.

Step Functions handles orchestration, retry logic, and error routing between stages so none of that complexity lives inside the Lambda functions themselves. Each function does exactly one job.

### Events: Amazon EventBridge

The Step Functions pipeline publishes events to a custom EventBridge event bus at completion and on failure. A photo.processed event fires when a photo clears all six pipeline stages. A photo.pipeline.failed event fires if any stage errors out. Downstream consumers like SNS notifications and analytics handlers subscribe to these events independently, with no direct coupling to the pipeline itself. This fan-out pattern means adding a new consumer requires zero changes to the pipeline code.

### AI and Machine Learning: Rekognition, Bedrock, Titan, and Translate

This is where the stack goes beyond a basic buy-and-sell marketplace. Four AI and ML services are integrated directly into the photo pipeline.

**Amazon Rekognition** runs content moderation on every uploaded photo before it reaches the catalogue. The pipeline stops immediately if explicit or sensitive content is detected. The photo is quarantined in DynamoDB and never published. This happens automatically with no human review queue required for clean content.

**Amazon Bedrock (Claude)** handles AI enrichment. After a photo passes moderation and processing, Bedrock analyzes the image and generates a title, description, and keyword tags. Photographers get a starting point rather than a blank form. This is the difference between a tool that helps photographers work faster than one that just stores their files.

**Amazon Titan Embeddings** generates a vector representation of each photo, stored alongside the metadata record. This powers the "More like this" feature on the customer portal, a semantic similarity search that finds visually related photos without relying on manually assigned tags.

**Amazon Translate** handles internationalization at deploy time, not at runtime. A single master file of English UI strings (en.json) is translated into 49 languages during the deployment process and saved as static JSON to S3. Every user gets their language served from CloudFront with zero per-request translation cost. At scale, the difference between calling Translate on every page load versus serving a cached file from edge is significant.

### Observability: AWS X-Ray

All 37 Lambda functions have X-Ray tracing enabled at the infrastructure level via Tracing: Active in the SAM globals, so every time a function runs it generates a trace segment automatically. The eleven core functions go further and import the X-Ray SDK, calling patch_all(), which instruments every boto3 call as a named subsegment. A DynamoDB read, an S3 put, a Rekognition call, each appears as its own timed node in the trace.

The Step Functions pipeline has tracing enabled end-to-end, so an entire upload workflow from API Gateway through six Lambda stages appears as a single connected trace in the X-Ray console. Custom annotations on each segment allow filtering traces by business context. When something goes wrong, the Service Map shows which node is red before you open a single log file.

---

## Multi-Region by Default

The entire infrastructure is defined in a single SAM template with conditional logic that controls which resources deploy in each region. The primary region gets everything. The secondary region gets the compute and data replication layers, but not the resources that only need to exist once like the primary Cognito pool.

Deploying to a second region is a single command:

```bash
sam deploy --config-env euwest1 \
  --parameter-overrides \
    PrimaryGalleriaUserPoolArn=[value] \
    PrimaryEventBusArn=[value]
```

---

## What I Learned

A few things that were not obvious going in.

**SAM template size compounds quickly.** A 2,600-line CloudFormation template with 37 functions, 9 DynamoDB tables, 4 CloudFront distributions, and WAFv2 ACLs is genuinely hard to reason about. The discipline of keeping functions single-purpose and naming everything consistently pays off when you are scanning 2,000 lines looking for a misconfigured IAM policy.

**Least-privilege IAM takes time.** Writing scoped IAM roles for 37 functions is tedious. It is also what separates a production-grade architecture from a demo. I do not regret the time spent on it.

**GlobalTables add complexity to schema design.** With active-active replication, you cannot rely on the database enforcing uniqueness across regions. You have to build duplicate-safe logic into the application layer. More on this in the payments article.

---

## What's Next

The next article in this series is where things get honest. Building this stack meant hitting real bugs, silent Lambda failures, CORS errors masking the actual HTTP status code, a missing IAM policy that made every photo's color mood default to neutral with no error message anywhere. Each one taught me something about AWS that a course alone would not have surfaced.

If you are studying for AWS certifications or building your first serverless project, I hope this series is useful. The full source code is on GitHub at [github.com/jvhammond2/serverless-photo-galleria](https://github.com/jvhammond2/serverless-photo-galleria).

---

*Joel is a cloud developer and AWS Solutions Architect candidate building production-grade serverless applications. He is a selected developer on the Digital Cloud Training collaborative program.*
