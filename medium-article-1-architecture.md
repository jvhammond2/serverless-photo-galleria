# How I Built a Production-Grade Serverless Photography Marketplace on AWS — Part 1: Architecture

*This is the first in a series of articles documenting the architecture and engineering decisions behind Galleria, a serverless fine art photography marketplace I built on AWS. Each article digs into one layer of the stack.*

---

When most people think of building a marketplace, they think of servers — EC2 instances, load balancers, maybe a managed Kubernetes cluster. I went the opposite direction. Every piece of Galleria runs on fully managed, event-driven AWS services. No servers to patch. No idle compute costs. No infrastructure to babysit at 2 a.m.

Here's how it's structured, and why I made the choices I did.

---

## What Is Galleria?

Galleria is a fine art photography marketplace connecting professional photographers with collectors and commercial buyers. It has two completely separate portals:

- **The Photographer Portal** — where photographers upload, manage, and monetize their work
- **The Customer Portal** — where buyers browse, search, and purchase photos

These aren't just two pages of the same app. They're two isolated tiers with separate authentication systems, separate access controls, and zero lateral connection between them. A buyer's account cannot touch anything on the photographer side — by design.

---

## Why Serverless?

Before getting into the architecture, it's worth answering the obvious question: *why serverless at all?*

Three reasons drove that decision:

**Cost at low traffic.** A traditional server sits idle and runs up a bill regardless of whether anyone is using the app. Lambda charges only for actual execution time, billed in milliseconds. For a marketplace that isn't yet running thousands of requests per hour, the difference is significant.

**Operational simplicity.** AWS manages patching, scaling, and availability for every service in this stack. That frees up all available engineering time for features rather than infrastructure maintenance.

**AWS certification alignment.** I'm actively studying for the AWS Solutions Architect Associate (SAA-C03) and Developer Associate (DVA-C02) certifications. Building real infrastructure with the services on those exams is a far better study method than flashcards.

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

Each portal authenticates through a **physically separate Amazon Cognito user pool**. This means:

- A compromised buyer account has no path into the photographer dashboard
- Photographers cannot access buyer order data
- Each pool has its own client IDs, scopes, and JWT tokens

This pattern — dual pool isolation — is something I see referenced in AWS security documentation but rarely implemented fully in personal projects. It adds setup complexity upfront and pays for itself the moment you start thinking about what happens when credentials are stolen.

Both pools connect to the same **API Gateway**, which sits behind a **WAFv2 Web ACL** with rate limiting rules. Every API endpoint validates the Cognito JWT before the Lambda function even receives the request.

---

## The Core Infrastructure Components

### Compute: AWS Lambda (Python 3.13)

The entire backend runs on Lambda. There are 37 functions in total, each scoped to a single responsibility: one for upload URL generation, one for search, one for payments, one for profile management, and so on.

Every function has its own IAM execution role granting only the specific actions it needs on the specific resources it touches — not a shared admin role, not a wildcard policy. This is least-privilege IAM in practice, and it's one of the key principles tested on the SAA-C03 exam.

### Database: DynamoDB GlobalTables

All application data lives in DynamoDB, configured as GlobalTables with active-active replication between **us-east-1** (primary) and **eu-west-1** (secondary). Writes in either region automatically replicate to the other with sub-second latency.

The tables include:

| Table | Purpose |
|---|---|
| PhotoMetadataTable | Photo catalogue, tags, pricing, pHash, GPS |
| OrdersTable | Completed purchase records |
| CollectionsTable | Buyer collections (buyerId + photoId composite key) |
| FollowsTable | Photographer follow relationships |
| SeriesTable | Photo essay series with GSI on photographerId |
| UserConsentTable | GDPR consent records |
| AuditLogTable | Append-only compliance audit log (7-year TTL) |

DynamoDB's pricing model — pay per read/write capacity consumed — fits the serverless cost philosophy perfectly.

### Storage: Amazon S3 (Zero Public Access)

Photos live in two S3 buckets: one for originals, one for processed thumbnails. Both buckets have public access blocked entirely. No one can reach an image by guessing its URL.

All asset access is mediated through CloudFront using **Origin Access Control (OAC)** — the current recommended replacement for the older Origin Access Identity (OAI) pattern. Photographers get pre-signed PUT URLs to upload directly to S3 without routing files through Lambda. Buyers get short-lived pre-signed GET URLs (300-second TTL) for downloads.

### CDN: Amazon CloudFront

Four CloudFront distributions serve the application:
- Photographer portal SPA
- Customer portal SPA
- Photo thumbnails
- Original photo downloads

Each distribution is protected by a WAFv2 Web ACL. The frontend files (HTML, JS) are deployed directly to S3 and served through CloudFront — no web servers, no containers.

### Orchestration: AWS Step Functions

When a photographer uploads a photo, an S3 event triggers a Step Functions Express Workflow that runs the image through a multi-step processing pipeline:

```
S3 Upload Event
      │
      ▼
Step Functions Pipeline
  ├── Blur
  ├── Crop
  ├── Resize
  ├── Rotate
  ├── Watermark (+ EXIF copyright embed)
  └── Compress
      │
      ▼
S3 Thumbnails Bucket → CloudFront
```

Step Functions handles the orchestration, retry logic, and error handling between steps — none of that complexity lives in the Lambda code itself.

### Events: Amazon EventBridge

Cross-region coordination happens through EventBridge. When something significant occurs in the primary region — a new photographer registration, a purchase — an event is published to a custom event bus and routed to the secondary region. This keeps both regions in sync beyond what DynamoDB GlobalTables handles at the data layer.

---

## Multi-Region by Default

The entire infrastructure is defined in a single SAM template with conditional logic that controls which resources deploy in each region. The primary region gets everything. The secondary region gets the compute and data replication layers, but not the resources that only need to exist once (like the primary Cognito pool).

Deploying to a second region is a single command:

```bash
sam deploy --config-env euwest1 \
  --parameter-overrides \
    PrimaryGalleriaUserPoolArn=[value] \
    PrimaryEventBusArn=[value]
```

---

## What I Learned

A few things that weren't obvious going in:

**SAM template size compounds quickly.** A 2,600-line CloudFormation template with 37 functions, 9 DynamoDB tables, 4 CloudFront distributions, and WAFv2 ACLs is genuinely hard to reason about. The discipline of keeping functions single-purpose and naming everything consistently pays off when you're scanning 2,000 lines looking for a misconfigured IAM policy.

**Least-privilege IAM takes time.** Writing scoped IAM roles for 37 functions is tedious. It's also what separates a production-grade architecture from a demo. I don't regret the time spent on it.

**GlobalTables add complexity to schema design.** With active-active replication, you can't rely on the database enforcing uniqueness across regions — you have to build idempotency into the application layer. More on this in the payments article.

---

## What's Next

The next article in this series covers the image processing pipeline in depth — how Step Functions orchestrates the multi-step workflow, how watermarks are applied and copyright data is embedded in EXIF, and how perceptual hashing works for near-duplicate image detection.

If you're studying for AWS certifications or building your first serverless project, I hope this series is useful. The full source code is on GitHub at [github.com/jvhammond2/serverless-photo-galleria](https://github.com/jvhammond2/serverless-photo-galleria).

---

*Joel Hammond is a cloud developer studying for AWS SAA-C03 and DVA-C02 certifications while building production-grade serverless applications.*
