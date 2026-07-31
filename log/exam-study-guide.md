# AWS Exam Study Guide — Serverless Photo Galleria
## SAA-C03 (Solutions Architect Associate) + SAP-C02 (Solutions Architect Professional)

**How to use this guide:** Every service is anchored to something you actually built. Start with the "Your project" line to recall the real context, then study the exam patterns. The SAA-C03 column tests *knowledge*. The SAP-C02 column tests *judgment under constraints*.

---

## Table of Contents
1. [AWS Lambda](#1-aws-lambda)
2. [API Gateway](#2-api-gateway)
3. [Amazon DynamoDB](#3-amazon-dynamodb)
4. [Amazon S3](#4-amazon-s3)
5. [Amazon CloudFront](#5-amazon-cloudfront)
6. [Amazon Cognito](#6-amazon-cognito)
7. [AWS Step Functions](#7-aws-step-functions)
8. [Amazon EventBridge](#8-amazon-eventbridge)
9. [Amazon Rekognition](#9-amazon-rekognition)
10. [Amazon Bedrock](#10-amazon-bedrock)
11. [Amazon Titan Embeddings](#11-amazon-titan-embeddings)
12. [Amazon Translate](#12-amazon-translate)
13. [AWS X-Ray](#13-aws-x-ray)
14. [AWS WAF](#14-aws-waf)
15. [Amazon SNS](#15-amazon-sns)
16. [AWS IAM](#16-aws-iam)
17. [AWS SAM / CloudFormation](#17-aws-sam--cloudformation)
18. [Multi-Region Architecture](#18-multi-region-architecture)
19. [Well-Architected Framework Mapping](#19-well-architected-framework-mapping)
20. [Exam-Day Quick Reference](#20-exam-day-quick-reference)

---

## 1. AWS Lambda

**Your project:** ~30 Python 3.12 functions handling every piece of business logic — upload URLs, photo processing, search, auth, payments, profile management, and more. Each deployed via SAM with individual IAM execution roles.

### SAA-C03 Patterns
- **Invocation types:** Synchronous (API Gateway → Lambda), asynchronous (S3 event → Lambda, EventBridge → Lambda), poll-based (SQS → Lambda). Know which is which and what retry behavior each has.
- **Cold starts:** Lambda initializes a new execution environment when no warm instance is available. Init time is outside your billed duration but adds latency. Mitigations: Provisioned Concurrency (keeps instances warm, costs money), SnapStart (Java only), smaller deployment packages.
- **Concurrency limits:** Default 1,000 concurrent executions per region per account. Reserve concurrency for critical functions; throttle low-priority ones.
- **Execution role:** The IAM role the function assumes at runtime. You saw this — missing S3ReadPolicy on TaggingFunction caused silent AccessDenied.
- **Layers:** Shared dependencies (like `aws-xray-sdk`) can be packaged as a Lambda Layer instead of bundled in every function's zip.
- **Timeout:** Max 15 minutes. Your processing Lambda (Pillow image manipulation + S3 upload) could approach this for large photos.

### SAP-C02 Patterns
- **When NOT to use Lambda:** Functions exceeding 15 min timeout, workloads needing >10 GB memory, stateful long-running processes, or workloads with consistent high baseline traffic where EC2 + Auto Scaling is cheaper.
- **Lambda@Edge vs. CloudFront Functions:** Lambda@Edge runs in regional edge caches (full Lambda runtime, up to 5 sec timeout). CloudFront Functions run at every PoP (JS only, sub-millisecond, for header manipulation and URL rewrites). Your WAF + CloudFront setup could be extended with either.
- **Event source mapping at scale:** For SQS → Lambda, tune batch size and concurrency to avoid function throttling causing queue buildup. The exam gives you a scenario where messages are piling up and asks you to fix it.
- **Organizational cost optimization:** At high volume, Lambda per-invocation pricing vs. Fargate vs. EC2 Spot. Know the breakeven points conceptually.

**Common exam trap:** Lambda has a 512 MB `/tmp` storage limit by default (configurable to 10 GB). If your processing Lambda writes temp files for large TIFFs, this is a real constraint.

---

## 2. API Gateway

**Your project:** Single REST API with two Cognito authorizers — one for PhotographerCognito, one for CustomerCognito. WAF sits in front via CloudFront. GatewayResponse resources add CORS headers to 4xx/5xx errors.

### SAA-C03 Patterns
- **REST vs. HTTP API vs. WebSocket API:** REST API = full features (WAF, usage plans, caching, request transformation). HTTP API = cheaper, faster, fewer features (no request transformation, no usage plans). WebSocket = bidirectional persistent connections. Your project uses REST.
- **Authorizers:** Cognito User Pool authorizer (validates JWT automatically, zero Lambda needed), Lambda authorizer (custom auth logic), IAM auth (SigV4). You used Cognito authorizer.
- **CORS:** Must be enabled on the API AND Lambda must return `Access-Control-Allow-Origin` in the response. GatewayResponseDefault4XX/5XX adds CORS to error responses that never reach Lambda — this is the fix you applied.
- **Caching:** API Gateway can cache responses by endpoint + query string. Reduces Lambda invocations. Useful for search results with the same parameters.
- **Usage plans + API keys:** Rate limiting and quota management per client. Not used in your project but exam-relevant.
- **Stage variables:** Environment-specific config (e.g., Lambda alias, DynamoDB table name) injected at deployment.

### SAP-C02 Patterns
- **API Gateway vs. ALB:** ALB can also front Lambda. Use ALB when you need advanced routing (path/header/host conditions), WebSocket with long connections, or you're already running EC2 targets. Use API Gateway when you need request transformation, per-method auth, or usage plans.
- **Private APIs:** API Gateway supports VPC endpoint integration so the API is only accessible within a VPC — not needed for a public marketplace but common in enterprise exam scenarios.
- **Throttling behavior:** 10,000 RPS default per account per region, 5,000 burst. Per-stage and per-method throttling overrides. The exam asks: what happens when you burst past the limit? (Answer: 429 Too Many Requests, not a 5xx.)

**Common exam trap:** API Gateway integration timeout is 29 seconds maximum. Lambda can run up to 15 minutes, but API Gateway will time out and return a 504 after 29 seconds. For long-running operations (like your processing pipeline), the correct pattern is: API Gateway → Lambda → starts async Step Functions → returns 202 Accepted immediately.

---

## 3. Amazon DynamoDB

**Your project:** Multiple tables (Photos, Profiles, Series, Follows, Collections, Likes, Cart) deployed as Global Tables replicating between us-east-1 and eu-west-1. GSIs for searching by photographer, category, colorMood.

### SAA-C03 Patterns
- **Primary key:** Partition key alone (simple) or partition key + sort key (composite). Good partition key design distributes writes evenly — a "hot partition" (e.g., `category = "landscape"` for every photo) throttles that partition.
- **GSI vs. LSI:** GSI = new partition key, can be added anytime, eventually consistent reads only. LSI = same partition key, different sort key, must be defined at table creation, supports strongly consistent reads. Your search Lambda uses GSIs.
- **Read consistency:** Eventually consistent (default, half the read cost) vs. strongly consistent (double the cost, not available on GSIs). For a photo marketplace, eventual consistency is fine on search; you'd want strong consistency on purchase confirmation.
- **Capacity modes:** On-demand (pay per request, auto-scales, higher per-request cost) vs. provisioned (set RCU/WCU, cheaper at steady load, can use Auto Scaling). Your project uses on-demand for cost efficiency in development.
- **DynamoDB Streams:** Change data capture — every write emits a stream record. Used to trigger downstream processing (e.g., fan out to EventBridge, replicate to another table).
- **TTL:** Automatically delete items after a timestamp attribute expires. Useful for cart items, session tokens, temporary data.

### SAP-C02 Patterns
- **Global Tables:** Multi-master replication. Any region can write. Conflict resolution uses last-writer-wins based on timestamp. The exam asks: what happens if two regions write the same item simultaneously? (Answer: last write wins — design your data model to avoid conflicts.)
- **Data residency:** Global Tables replicate everywhere the table exists. If GDPR requires EU data to stay in eu-west-1, you cannot use Global Tables for that data — you'd need separate regional tables with application-level routing.
- **DynamoDB vs. RDS:** DynamoDB = schemaless, scales horizontally, millisecond latency, no JOINs. RDS = relational, complex queries, ACID transactions across tables. For a photo marketplace, DynamoDB is correct; for a financial system with complex reporting, RDS is better.
- **Single-table design:** Advanced pattern — all entities in one table, differentiated by key structure (e.g., `PK=USER#123, SK=PHOTO#456`). Reduces GSIs and improves query efficiency. SAP-C02 may test awareness of this pattern.
- **DAX (DynamoDB Accelerator):** In-memory cache in front of DynamoDB, microsecond reads. Write-through cache. The exam asks when to add DAX: when you have hot reads on the same items (e.g., a viral photo being viewed thousands of times/second).

**Common exam trap:** GSIs do NOT support strongly consistent reads. If a question requires strong consistency on a non-primary-key attribute, that's a red flag against GSI — consider redesigning the primary key or using a different storage engine.

---

## 4. Amazon S3

**Your project:** Four buckets per region — originals (private, presigned URL access), thumbs (CloudFront OAC), frontend (PP static hosting), purchaser (CP static hosting). Presigned URLs for upload and download. Translation JSON files served from a fifth bucket.

### SAA-C03 Patterns
- **Bucket policies vs. IAM policies:** Bucket policy = resource-based, attached to the bucket, can grant cross-account access. IAM policy = identity-based, attached to a user/role. Both can allow/deny. Use bucket policy to restrict access to a specific VPC endpoint or specific IP range.
- **OAC vs. OAI:** Origin Access Control (OAC) is the modern replacement for Origin Access Identity (OAI) for restricting S3 bucket access to CloudFront only. Your thumbs bucket uses OAC.
- **Presigned URLs:** Temporary access URLs signed with IAM credentials. You used them for upload (PUT) and download (GET). Expiry is configurable. Key fact: the presigned URL is valid even if the generating IAM user's permissions are later revoked — until the URL expires.
- **S3 storage classes:** Standard (frequent access), Intelligent-Tiering (auto-moves between tiers), Standard-IA (infrequent access, retrieval fee), Glacier Instant/Flexible/Deep Archive (archival, increasing retrieval time and cost savings). Lifecycle policies automate transitions.
- **Versioning:** Keeps all versions of an object. Required for MFA Delete and Cross-Region Replication. Increases storage cost.
- **Server-side encryption:** SSE-S3 (AWS manages keys), SSE-KMS (you control keys via KMS, audit trail, extra cost), SSE-C (you provide the key per request). Default encryption is now SSE-S3 for all buckets.
- **Event notifications:** S3 can trigger Lambda, SQS, or SNS on object creation/deletion. Your upload pipeline is triggered this way.

### SAP-C02 Patterns
- **Multi-region replication:** S3 Cross-Region Replication (CRR) replicates objects asynchronously to another region. Requires versioning enabled. Does NOT replicate existing objects (only new ones after replication is configured) — this trips people up.
- **S3 Access Points:** Named entry points with their own policies, simplifying access management for shared buckets (e.g., one access point per application team). Enterprise pattern.
- **Object Lock / WORM:** Write Once Read Many — objects cannot be deleted or modified for a specified period. Required for regulatory compliance (SEC Rule 17a-4, HIPAA). Not in your project but SAP-C02 tests it.
- **Cost optimization at scale:** For a photo marketplace serving millions of photos, the question is whether to use CloudFront to reduce S3 data transfer costs (CloudFront → S3 is free; S3 → internet is not). You already do this correctly.

**Common exam trap:** S3 is eventually consistent for overwrite PUTs and DELETEs (in old versions of S3). Since December 2020, S3 provides strong read-after-write consistency for all operations. This may still appear in older practice questions — the current answer is strong consistency.

---

## 5. Amazon CloudFront

**Your project:** Four distributions per region — PP frontend, CP purchaser, API/WAF-fronted, thumbs CDN. Separate distributions because each has different origins, cache behaviors, and access control requirements.

### SAA-C03 Patterns
- **Origin types:** S3 bucket, S3 website endpoint, ALB, API Gateway, custom HTTP origin. Your distributions use S3 and API Gateway origins.
- **Cache behaviors:** Rules matching URL path patterns, each with its own TTL, allowed methods, and origin. E.g., `/api/*` → no cache, forward to API Gateway; `/*` → long TTL, serve from S3.
- **Invalidations:** Force edge locations to re-fetch from origin. You ran these after every deploy. Cost: first 1,000 paths/month free, then $0.005/path.
- **Geo-restriction:** Block or allow specific countries. WAF gives more granular control.
- **Signed URLs vs. signed cookies:** Signed URL = access one specific file. Signed cookie = access multiple files (e.g., all content in a paid subscription). For download delivery, signed URL is correct (one photo at a time).
- **CloudFront Functions vs. Lambda@Edge:** (See Lambda section above.)
- **HTTPS enforcement:** Redirect HTTP to HTTPS at the distribution level. You do this.

### SAP-C02 Patterns
- **Origin failover:** Configure a primary and secondary origin. If the primary returns 5xx, CloudFront automatically retries with the secondary. Use for high availability without DNS failover delay.
- **Multi-origin patterns:** One distribution can route different paths to different origins — API to API Gateway, static assets to S3, media to a media server. Reduces the number of domains clients need to know about.
- **Field-level encryption:** Encrypt specific POST body fields at the CloudFront edge, so even Lambda can't read them in plaintext — only the application with the private key can decrypt. Used for PCI-DSS compliance (credit card numbers).
- **Cost at scale:** CloudFront has 13 price classes. Price Class All (all edge locations, highest cost, lowest latency globally) vs. Price Class 100 (only cheapest regions). For a photo marketplace targeting global buyers, the exam asks which to choose and why.

**Common exam trap:** CloudFront distributions cannot be deleted — they can only be disabled. A disabled distribution still exists and can be re-enabled. This affects cost calculations in exam scenarios about "removing" a distribution.

---

## 6. Amazon Cognito

**Your project:** Two separate User Pools — PhotographerCognito (`us-east-1_MMG0QjcVQ`) and CustomerCognito (`us-east-1_WIZjOvOEF`). API Gateway uses Cognito authorizers so photographer JWTs are never accepted on customer routes and vice versa.

### SAA-C03 Patterns
- **User Pool vs. Identity Pool:** User Pool = user directory, handles sign-up/sign-in, issues JWTs. Identity Pool = exchanges tokens (from User Pool, Google, Facebook, SAML, etc.) for temporary AWS credentials (STS). You used User Pools only.
- **JWT tokens:** Cognito issues three tokens: ID token (user identity claims), Access token (API authorization), Refresh token (get new ID/Access tokens without re-login). API Gateway validates the ID or Access token.
- **Hosted UI:** Cognito can serve a pre-built sign-in/sign-up UI. You built a custom UI in `index.html` and `customer.html` using the Cognito JS SDK instead.
- **User Pool triggers:** Lambda functions that fire at lifecycle events — pre-sign-up, post-confirmation, pre-token-generation, post-authentication. Can be used to add custom claims to JWTs or enforce business rules.
- **MFA:** Cognito supports TOTP (authenticator apps) and SMS MFA. Can be required or optional per User Pool.
- **Groups:** Assign users to groups (e.g., `Admins`, `PremiumPhotographers`). Groups appear as claims in the JWT, so API Gateway authorizer or Lambda can check group membership.

### SAP-C02 Patterns
- **Federated identity:** Cognito Identity Pools support SAML 2.0 and OIDC, enabling enterprise SSO (e.g., employees sign in with Okta, receive temporary AWS credentials). Common in enterprise exam scenarios.
- **Why two User Pools instead of one with groups?** Groups in a single pool would work, but separate pools give you independent password policies, MFA requirements, token expiry, and audit trails. Also, a bug in one pool can't affect the other. SAP-C02 tests this design judgment.
- **Cross-region Cognito:** User Pools are regional. There's no native cross-region replication. For a multi-region app requiring the same user credentials in both regions, you must either: (a) use one User Pool in one region and accept cross-region latency for auth, or (b) sync users via Lambda trigger + DynamoDB + custom logic. Your project uses option (a) — both eu-west-1 and us-east-1 point to the same us-east-1 Cognito pools.
- **Advanced security features:** Cognito can detect compromised credentials, unusual sign-in locations, and bot sign-up attempts. Enterprise requirement, exam scenario.

**Common exam trap:** Cognito User Pool tokens expire. The ID and Access tokens default to 1 hour. The Refresh token defaults to 30 days. If a user's session expires and the app doesn't refresh the token, the next API call returns 401. This is what caused your "Save Tags 401" error — a stale session token.

---

## 7. AWS Step Functions

**Your project:** 6-stage upload pipeline — ModerateContent → ProcessImage → TagImage → EnrichWithAI → EmbedPhoto → NotifyProcessed. Orchestrates Lambda functions with retry logic and error handling. X-Ray tracing enabled.

### SAA-C03 Patterns
- **Standard vs. Express workflows:** Standard = durable, exactly-once execution, up to 1 year duration, audit history in console, higher cost. Express = at-least-once, up to 5 minutes, high throughput (100,000/sec), lower cost. Your upload pipeline uses Standard (needs exactly-once and audit visibility).
- **Task states:** Call a Lambda, ECS task, DynamoDB, S3, SNS, SQS, Bedrock, and more natively. No glue Lambda needed for many integrations.
- **Retry and Catch:** Each state can define retry policies (max attempts, backoff) and Catch blocks (on error type → go to this state). Your pipeline uses Catch to route failed photos to a quarantine path.
- **Wait state:** Pause execution for a duration or until a callback token is received. Use for human approval workflows or waiting for an external system.
- **Map state:** Run a set of steps in parallel for each item in an array. If you needed to generate 5 different thumbnail sizes in parallel, Map would be the right tool.
- **Parallel state:** Run multiple branches simultaneously and wait for all to complete before continuing.

### SAP-C02 Patterns
- **Step Functions vs. SQS+Lambda chain:** Step Functions = centralized orchestration, full visibility, easier error handling, higher cost. SQS+Lambda = decentralized choreography, cheaper at scale, harder to trace failures, no built-in retry coordination across steps. Use Step Functions when you need to see the state of a specific execution (e.g., "where did this photo's pipeline fail?"). Use SQS when throughput and cost matter more than visibility.
- **Step Functions vs. EventBridge Pipes:** EventBridge Pipes = simple point-to-point event routing with optional transformation. Step Functions = complex multi-step orchestration. Not interchangeable.
- **Distributed Map:** Process millions of items from S3 (e.g., reprocess every photo in the catalog after a new AI model is deployed). Runs child executions in parallel at massive scale.
- **Callback pattern:** Lambda starts an external process (e.g., a human review), returns a task token, and Step Functions waits. When the external process completes, it calls `SendTaskSuccess` with the token. Used in your moderation flow if you wanted human review of borderline content.

**Common exam trap:** Step Functions Standard workflows charge per state transition. A 6-state pipeline = 6 transitions per execution. At millions of photos/day, this cost adds up — the exam may ask you to optimize cost by combining states or switching to Express Workflows for high-volume workloads.

---

## 8. Amazon EventBridge

**Your project:** `photo.processed` and `photo.pipeline.failed` events published to a custom event bus (`serverless-photo-galleria-events-us-east-1`). Decouples the pipeline from downstream consumers (SNS notifications, analytics).

### SAA-C03 Patterns
- **Event bus types:** Default event bus (AWS service events), custom event bus (your application events), partner event bus (SaaS integrations like Shopify, Zendesk).
- **Rules:** Pattern-match incoming events and route to targets. E.g., `{ "detail-type": ["photo.pipeline.failed"] }` → SNS topic → email notification.
- **Targets:** Lambda, SQS, SNS, Step Functions, API Gateway, Kinesis, and more. One rule can fan out to 5 targets simultaneously.
- **Event pattern matching:** Filter by any field in the event JSON — source, detail-type, specific detail field values. Very flexible.
- **Scheduled rules:** Cron or rate expressions. E.g., `rate(1 day)` → Lambda to generate daily earnings report. Replaces CloudWatch Events (which is the same service, just rebranded).

### SAP-C02 Patterns
- **EventBridge vs. SNS vs. SQS:** SNS = pub/sub fan-out, push to multiple subscribers, no filtering beyond subscription filter policies. SQS = queue, pull-based, exactly once (FIFO) or at-least-once (Standard), decouples producer from consumer rate. EventBridge = event bus with rich pattern matching and routing, integrates with 200+ AWS services and SaaS. Use EventBridge when you need content-based routing; use SNS for simple fan-out; use SQS when you need queuing/buffering.
- **Cross-account event routing:** EventBridge supports sending events from one account's bus to another account's bus. Essential in multi-account organizations for centralized event processing.
- **EventBridge Pipes:** Connect an event source (SQS, DynamoDB Stream, Kinesis) directly to a target with optional filtering and enrichment (via Lambda or Step Functions), without writing glue code.
- **Archive and replay:** EventBridge can archive events and replay them later — useful for re-processing after a bug fix (e.g., re-run all `photo.processed` events from the last 7 days through an updated consumer Lambda).

**Common exam trap:** EventBridge has a 256KB event payload limit. For large payloads, the pattern is to store the data in S3 and put the S3 reference in the EventBridge event (a "claim check" pattern).

---

## 9. Amazon Rekognition

**Your project:** Content moderation in the pipeline — scans every uploaded photo for explicit/sensitive content before it's published. Flags photos are quarantined; the pipeline stops at that stage.

### SAA-C03 Patterns
- **Rekognition Image vs. Video:** Image = synchronous, analyze a single frame. Video = asynchronous, analyze a video file stored in S3, returns a job ID, notifies via SNS when complete.
- **Features:** Label detection (objects/scenes), face detection/comparison/search, text detection, content moderation (inappropriate content), celebrity recognition, PPE detection.
- **Confidence scores:** Every detection result includes a confidence percentage. Your moderation Lambda likely filters above a threshold (e.g., 80% confidence of explicit content) before flagging.
- **Custom Labels:** Train Rekognition on your own dataset to detect custom objects/scenes not in the default model.
- **API call, not a deployed resource:** Rekognition is a managed API. You call it with an image (bytes or S3 reference). No infrastructure to manage or scale.

### SAP-C02 Patterns
- **Cost at scale:** Rekognition charges per image analyzed. At 1M uploads/month, moderation cost is significant. Optimization: run a lightweight local check first (file size, format validation) before calling Rekognition. Or use Rekognition Custom Labels only for domain-specific detection.
- **Rekognition vs. Bedrock for image analysis:** Rekognition = purpose-built vision APIs (moderation, faces, labels, text). Bedrock = general multimodal AI (describe, explain, generate). Your project uses both — Rekognition for moderation (fast, cheap, specialized) and Bedrock for enrichment (expensive, flexible, generative). The exam may ask you to choose — pick Rekognition for any structured vision task, Bedrock for unstructured generative output.
- **Privacy and compliance:** Rekognition face data — the FaceIndex and SearchFacesByImage APIs store and match facial embeddings. GDPR implications. Understand the difference between analyzing faces (stateless, no storage) and indexing faces (stores a vector, subject to data retention rules).

---

## 10. Amazon Bedrock

**Your project:** Claude model via Bedrock generates titles, descriptions, and keyword tags from photo content during the EnrichWithAI pipeline stage. Also provides AI composition feedback in the lightbox.

### SAA-C03 Patterns
- **What Bedrock is:** Managed API for foundation models (FMs) — Anthropic Claude, Amazon Titan, Stability AI, Meta Llama, Cohere, and more. No infrastructure to manage, no model training required (for base models).
- **Inference modes:** On-demand (pay per token), Provisioned Throughput (reserved capacity, consistent latency, higher cost). Use provisioned for latency-sensitive, high-volume workloads.
- **Model IDs:** You reference models by ID (e.g., `anthropic.claude-3-sonnet-20240229-v1:0`). Models are versioned — pin to a specific version in production to avoid behavior changes.
- **Knowledge Bases:** Connect Bedrock to a vector database (OpenSearch Serverless, Pinecone, etc.) for RAG (Retrieval-Augmented Generation). Not used in your project but exam-relevant.
- **Agents:** Bedrock Agents can autonomously call APIs (tools) to complete multi-step tasks. The exam asks about agents when a question involves an AI system that needs to take actions, not just generate text.

### SAP-C02 Patterns
- **Bedrock vs. SageMaker:** Bedrock = use pre-trained foundation models via API, no ML expertise required, limited customization. SageMaker = train, fine-tune, and host your own models, full control, requires ML expertise. Use Bedrock when you need generative AI quickly; use SageMaker when you need custom models or domain-specific fine-tuning.
- **Fine-tuning:** Bedrock supports fine-tuning some models on your own data to improve domain-specific accuracy. Requires labeled training data and incurs training costs.
- **Guardrails:** Bedrock Guardrails filter model inputs and outputs — block certain topics, remove PII, prevent harmful content. Relevant for compliance in enterprise scenarios.
- **Cross-region inference:** Bedrock model availability varies by region. Your eu-west-1 deployment needs Bedrock — check model availability per region. The exam may test awareness of this regional constraint.

**Common exam trap:** Bedrock is stateless — it doesn't remember previous conversations unless you pass the conversation history in each request. For a multi-turn AI assistant, you must maintain conversation history in your application and include it in each API call.

---

## 11. Amazon Titan Embeddings

**Your project:** Generates vector embeddings for each processed photo (via the EmbedPhoto pipeline stage) for semantic similarity search — the "More like this" feature on the customer portal.

### SAA-C03 Patterns
- **What embeddings are:** Numerical vector representations of data (text, images) where similar content has similar vectors. Semantic search finds items by meaning/similarity rather than keyword matching.
- **Vector search options on AWS:** OpenSearch Service with k-NN (k-nearest neighbors), pgvector on Aurora/RDS, or Amazon MemoryDB. Your project stores embeddings in DynamoDB and does similarity computation in Lambda — fine for small scale, not for production at millions of photos.
- **Titan Embeddings vs. Titan Text:** Titan Embeddings = converts input to a vector (no text output). Titan Text = generative text model. Different use cases.

### SAP-C02 Patterns
- **Scaling vector search:** DynamoDB + Lambda cosine similarity doesn't scale past a few thousand photos (you'd compute similarity against every item). Production pattern: store embeddings in OpenSearch Serverless (with k-NN enabled), query top-N similar vectors in milliseconds. The SAP exam may ask you to redesign your similarity search for 100M+ photos.
- **RAG architecture:** Retrieval-Augmented Generation — embed a knowledge base, store in a vector store, query with Titan Embeddings to find relevant context, pass context to Bedrock Claude to generate an answer grounded in your data. Common enterprise AI pattern tested on SAP-C02.

---

## 12. Amazon Translate

**Your project:** At deploy time, a script calls Translate to generate 49-language translations of all UI strings from `en.json`. Saved as static JSON to S3. Zero per-request translation cost.

### SAA-C03 Patterns
- **Batch vs. real-time translation:** Translate supports real-time (synchronous API call per string) and batch (S3 input → translate → S3 output, async). Your project uses real-time at deploy time to generate static files.
- **Custom terminology:** Upload a glossary of domain-specific terms that Translate should not translate (brand names, technical terms). Relevant for consistent translation of product names.
- **Active Custom Translation:** Train Translate on your own parallel corpus (source + target text pairs) for domain-specific accuracy improvement.

### SAP-C02 Patterns
- **Static translation vs. runtime translation:** Your approach (static files at deploy, served from S3/CloudFront) is optimal for a UI with a fixed set of strings. Runtime translation (call Translate on every page load) is used when content is user-generated and unpredictable. The exam asks you to choose based on cost, latency, and content characteristics.
- **Cost comparison:** S3/CloudFront serving a 20KB JSON file = fractions of a cent per 1M requests. Real-time Translate = $15 per 1M characters. For a fixed UI, static generation is orders of magnitude cheaper.

---

## 13. AWS X-Ray

**Your project:** `Tracing: Active` on all Lambdas, `TracingEnabled: true` on API Gateway, `Tracing: Enabled: true` on Step Functions. 11 functions use `patch_all()` to auto-instrument boto3 clients. `_annotate()` helper adds custom metadata (photoId, userId, operation) to segments.

### SAA-C03 Patterns
- **Segments vs. subsegments:** Segment = one unit of work in one service (Lambda function invocation, API Gateway request). Subsegment = a downstream call within that segment (DynamoDB GetItem, S3 PutObject, Rekognition DetectModerationLabels). `patch_all()` automatically creates subsegments for every boto3 call.
- **Service Map:** Visual graph of your architecture showing which services talk to each other, average latency, and error rate per connection. Best first stop for diagnosing unknown failures.
- **Traces:** End-to-end request path. A single trace can span multiple services — API Gateway → Lambda → DynamoDB → S3. Filter by time range, response code, annotation values.
- **Sampling:** X-Ray doesn't trace 100% of requests by default. Default rule: 1 request/sec + 5% of additional requests. Configure custom sampling rules to capture more or less based on URL, method, or service name.
- **Annotations vs. metadata:** Annotations = key-value pairs indexed for filtering (searchable in console). Metadata = arbitrary JSON not indexed. Use annotations for values you'll filter on (`photoId`, `userId`); use metadata for large debug payloads.

### SAP-C02 Patterns
- **X-Ray vs. CloudWatch:** X-Ray = distributed tracing, request-level visibility, cross-service latency breakdown. CloudWatch = metrics, logs, alarms, dashboards. They complement each other. CloudWatch tells you something is slow; X-Ray tells you which downstream call caused it.
- **X-Ray Groups:** Create subsets of traces matching a filter expression (e.g., all traces with `annotation.operation = "upload"`). Monitor error rate and latency for that group independently.
- **Insights:** X-Ray Insights automatically detects anomalies — sudden spikes in latency or error rate — and notifies via CloudWatch Events. Proactive detection without defining explicit alarms.
- **When X-Ray alone isn't enough:** X-Ray traces requests that reach your Lambda. It cannot trace client-side JavaScript errors, failed DNS lookups, or requests that never reach API Gateway. For those, use browser RUM (Real User Monitoring) tools or CloudWatch Synthetics.

**Key lesson from your project:** The colorMood bug (S3 AccessDenied swallowed silently) would have shown as a red fault subsegment in X-Ray. Check X-Ray before reading Lambda code when diagnosing silent failures.

---

## 14. AWS WAF

**Your project:** WAF WebACL fronting the API CloudFront distribution. Protects against common web exploits (SQL injection, XSS, rate-limiting abuse).

### SAA-C03 Patterns
- **WAF attach points:** CloudFront, ALB, API Gateway, AppSync, Cognito User Pool. You attached to CloudFront (edge protection, globally enforced before traffic reaches your origin).
- **Rules:** Managed rule groups (AWS or third-party, pre-built rule sets for OWASP Top 10, bot control, known bad inputs) and custom rules (your own conditions). Your stack uses managed rules.
- **Rule actions:** Allow, Block, Count (log but don't block, used for testing new rules), CAPTCHA, Challenge.
- **Rate-based rules:** Block IPs that exceed a request threshold in 5 minutes. Protects against DDoS and credential stuffing.
- **Web ACL capacity units (WCU):** Each rule consumes capacity. Default limit 1,500 WCU per Web ACL.

### SAP-C02 Patterns
- **WAF + Shield:** WAF = Layer 7 (application) protection, you write/select rules. Shield Standard = automatic Layer 3/4 (network) DDoS protection, free, always-on. Shield Advanced = enhanced DDoS protection + 24/7 DDoS response team + cost protection, $3,000/month. For a photo marketplace, WAF + Shield Standard is appropriate. The exam asks when to upgrade to Advanced.
- **Centralized WAF management:** In a multi-account organization, use AWS Firewall Manager to deploy the same WAF Web ACL across all accounts and regions from a central security account. Ensures consistent protection without per-account configuration.
- **Bot Control managed rule group:** Specifically detects and blocks bots (scrapers, credential stuffers, content theft). Relevant for protecting your photo watermarks and preventing bulk downloading.

---

## 15. Amazon SNS

**Your project:** Receives `photo.pipeline.failed` events from EventBridge and sends failure notifications (email/SMS to the photographer or admin).

### SAA-C03 Patterns
- **Topics and subscriptions:** Publishers send to a topic; subscribers receive from it. One message → all subscribers (fan-out). Subscription protocols: HTTP/S, email, SMS, SQS, Lambda, mobile push.
- **SNS + SQS fan-out pattern:** Publish to SNS, subscribe multiple SQS queues. Each queue feeds a different consumer Lambda. Ensures each consumer gets every message independently. Classic exam pattern.
- **Message filtering:** Subscription filter policies let each subscriber receive only messages matching certain attributes. E.g., one SQS queue only gets `pipeline.failed` events; another gets all events.
- **FIFO topics:** Ordered, exactly-once delivery (like SQS FIFO). More expensive, lower throughput.
- **SNS vs. SES:** SNS = programmatic notifications (SMS, push, email via subscription). SES (Simple Email Service) = bulk transactional email (marketing campaigns, receipts, custom HTML templates). For pipeline failure alerts, SNS is correct. For sending a purchase receipt email with your logo and branding, SES is correct.

### SAP-C02 Patterns
- **Dead-letter queues on SNS subscriptions:** If an SQS subscriber can't be reached (queue deleted, access denied), failed deliveries go to a DLQ for investigation.
- **SNS cross-account delivery:** SNS can deliver to SQS queues in different AWS accounts. Used in multi-account architectures for event routing without EventBridge.

---

## 16. AWS IAM

**Your project:** Every Lambda has its own execution role with least-privilege policies. The colorMood bug was caused by a missing `S3ReadPolicy` on TaggingFunction. SAM's policy templates (`S3ReadPolicy`, `DynamoDBCrudPolicy`) generate the correct IAM policies automatically.

### SAA-C03 Patterns
- **IAM entities:** Users (human identities), Groups (collections of users), Roles (assumed by services, cross-account access), Policies (JSON permission documents).
- **Policy types:** Identity-based (attached to user/role), Resource-based (attached to S3 bucket, SQS queue, Lambda, etc.), Permission boundaries (max permissions a role can have), SCPs (Organization-level guardrails), Session policies (passed when assuming a role).
- **Evaluation logic:** Explicit Deny > Allow > Implicit Deny. An explicit Deny anywhere in the policy chain overrides any Allow.
- **Least privilege:** Grant only the minimum permissions needed. Use `S3ReadPolicy` (read-only) for functions that only read; `DynamoDBCrudPolicy` (read/write) only for functions that need to write.
- **Cross-account access:** Role in Account A, trust policy allows Account B. Account B user/role assumes the role via STS. No long-term credentials shared.
- **MFA enforcement:** IAM condition `aws:MultiFactorAuthPresent: true` requires MFA for sensitive operations (delete S3 objects, deploy CloudFormation, etc.).

### SAP-C02 Patterns
- **Permission boundaries:** Set the maximum permissions a role can have, even if its identity policy would grant more. Enables developers to create roles themselves (self-service) without escalating privileges beyond what the permission boundary allows.
- **AWS Organizations SCPs:** Organization-wide guardrails that apply to all accounts in an OU (Organizational Unit). SCPs cannot grant permissions — they can only restrict what IAM policies in member accounts can allow. E.g., SCP preventing eu-west-1 Lambda functions from calling Bedrock outside eu-west-1.
- **Attribute-based access control (ABAC):** Use IAM tags to control access — e.g., a Lambda can only access DynamoDB items tagged with the same `Environment` tag. Scales better than resource-based policies in large organizations.
- **IAM Access Analyzer:** Scans policies and resource policies to find external access (unintended public exposure, cross-account access). Enterprise security tool.

**Common exam trap:** IAM is global (not regional). An IAM role created in us-east-1 works in eu-west-1. However, resource-specific policies (S3 bucket policy, DynamoDB resource policy) are regional — a policy on a us-east-1 DynamoDB table doesn't automatically apply to the eu-west-1 Global Table replica.

---

## 17. AWS SAM / CloudFormation

**Your project:** `template.yaml` defines every resource in the stack. SAM transforms (Lambda, API, Step Functions) expand into CloudFormation resources at deploy time. `samconfig.toml` stores deployment parameters for both regions.

### SAA-C03 Patterns
- **CloudFormation concepts:** Template → Stack → Resources. Change sets show you what will change before you deploy. Drift detection shows you what changed outside CloudFormation.
- **SAM vs. CloudFormation:** SAM is a superset — SAM templates are valid CloudFormation templates after the transform. SAM adds shorthand for Lambda (`AWS::Serverless::Function`), API (`AWS::Serverless::Api`), and more.
- **Outputs:** Export values from one stack, import into another via `Fn::ImportValue`. You used Outputs to capture CloudFront URLs, Cognito pool IDs, and S3 bucket names.
- **Parameters:** Values passed at deploy time. Your `IsPrimaryRegion` and `Environment` parameters control which resources are created in each region.
- **Conditions:** Create resources only when a condition is true. `Condition: IsPrimaryRegion` gates Canary and CloudWatch Synthetics resources so they only deploy in us-east-1.
- **Rollback:** By default, if any resource fails to create/update, CloudFormation rolls back the entire stack to the previous state.

### SAP-C02 Patterns
- **StackSets:** Deploy the same CloudFormation template to multiple accounts and regions from a single operation. Essential for multi-account organizations — instead of deploying your SAM template twice manually, StackSets can deploy it to 50 accounts simultaneously.
- **Nested stacks:** Break a large template into modular nested stacks (one for networking, one for compute, one for data). Reduces template size, allows independent updates.
- **Service Catalog:** Publish approved CloudFormation templates as products that teams self-serve, with guardrails. Enterprise governance pattern.
- **CDK vs. SAM vs. Terraform:** CDK = code-first (TypeScript, Python) IaC generating CloudFormation. SAM = YAML-first, optimized for serverless. Terraform = multi-cloud IaC, not CloudFormation-native. The exam doesn't test Terraform but may compare CDK and CloudFormation.

---

## 18. Multi-Region Architecture

**Your project:** Primary (us-east-1) hosts Cognito pools and DynamoDB Global Table primaries. Secondary (eu-west-1) replicates DynamoDB and runs its own Lambda, API Gateway, Step Functions, and CloudFront distributions — pointing to the same Cognito pools.

### SAA-C03 Patterns
- **Disaster recovery strategies (RTO/RPO):**
  - *Backup & Restore* — lowest cost, highest RTO/RPO. Hours to recover.
  - *Pilot Light* — minimal replica running (e.g., database only), scale up on failure.
  - *Warm Standby* — scaled-down full stack running in secondary region, scale up on failure.
  - *Multi-Site Active-Active* — full stack running in all regions, instant failover, highest cost.
  - Your project is closest to **Multi-Site Active-Active** (both regions serve traffic) but with a single Cognito pool in us-east-1 — a limitation worth noting.

- **Route 53 routing policies:**
  - *Latency* — route to the region with lowest latency for the user. Best for your global photo marketplace.
  - *Failover* — primary/secondary, switch on health check failure.
  - *Weighted* — percentage split (useful for canary deployments to a new region).
  - *Geolocation* — route based on user's geographic location (not latency — a user in the UK might have lower latency to us-east-1 than eu-west-1 depending on ISP).

### SAP-C02 Patterns
- **Data residency vs. latency:** Global Tables replicate to all configured regions. If GDPR requires EU data to stay in EU, you cannot use Global Tables with us-east-1 as a replica — you'd need separate regional tables and application-level routing by user location.
- **Active-active Cognito gap:** Your project's Cognito pools are only in us-east-1. If us-east-1 has an outage, users cannot sign in from eu-west-1 either. True active-active auth requires Cognito in both regions with user sync (complex, usually avoided in favor of accepting this single-region auth dependency).
- **RTO/RPO for serverless:** Lambda, API Gateway, and Step Functions are inherently multi-AZ within a region. The regional failure scenario is the one to plan for. Global Tables give you near-zero RPO for data; deploying your SAM stack in multiple regions gives you near-zero RTO for compute.

---

## 19. Well-Architected Framework Mapping

The 6 pillars map directly to decisions you made in this project. Know these for both exams.

| Pillar | Your Project Decision | Why It Matters |
|--------|----------------------|----------------|
| **Operational Excellence** | X-Ray tracing, CloudWatch logs, SAM IaC, session logs, CI/CD via `deploy.ps1` | Automate operations, make small reversible changes, anticipate failure |
| **Security** | Two Cognito pools, WAF, presigned S3 URLs, no public buckets, IAM least-privilege, JWT validation at API GW | Defense in depth, least privilege, protect data in transit and at rest |
| **Reliability** | Step Functions retry/catch, multi-region DynamoDB Global Tables, CloudFront origin failover, SNS failure notifications | Automatically recover from failure, scale horizontally, manage change |
| **Performance Efficiency** | CloudFront CDN for static assets, Lambda (no idle servers), DynamoDB on-demand (auto-scales), Titan Embeddings for vector search | Use computing resources efficiently, select the right resource type |
| **Cost Optimization** | Serverless (pay-per-use), static translation files (zero runtime translate cost), CloudFront reducing S3 egress, on-demand DynamoDB | Avoid unnecessary cost, measure efficiency, adopt consumption model |
| **Sustainability** | Serverless (no idle compute), CloudFront caching (reduces origin compute), on-demand scaling (no over-provisioning) | Minimize environmental impact of cloud workloads |

---

## 20. Exam-Day Quick Reference

### "Which service should I use?" Decision Trees

**Queue/messaging:**
- Need ordering + exactly-once → SQS FIFO
- Need fan-out to multiple consumers → SNS → multiple SQS queues
- Need content-based routing with 200+ integrations → EventBridge
- Need complex multi-step workflow with retry/catch → Step Functions

**Database:**
- Need millisecond latency, flexible schema, scale → DynamoDB
- Need complex queries, JOINs, ACID → RDS/Aurora
- Need time-series data → Timestream
- Need in-memory caching → ElastiCache (Redis or Memcached)
- Need vector search → OpenSearch with k-NN

**AI/ML:**
- Need object/face/text detection in images → Rekognition
- Need to generate text, summarize, classify → Bedrock
- Need to train a custom model → SageMaker
- Need vector embeddings → Titan Embeddings (via Bedrock)
- Need speech-to-text → Transcribe
- Need text-to-speech → Polly
- Need translation → Translate

**Compute:**
- Short tasks, event-driven, variable load → Lambda
- Long-running tasks (>15 min), containers → ECS Fargate
- Full OS control, steady high load → EC2 + Auto Scaling
- Batch jobs → AWS Batch

**CDN/edge:**
- Static assets, API acceleration, global distribution → CloudFront
- Need to run code at edge → Lambda@Edge or CloudFront Functions

### Numbers to Memorize

| Service | Key Limit |
|---------|-----------|
| Lambda | 15 min timeout, 10 GB memory, 10 GB /tmp, 250 MB deployment package unzipped |
| API Gateway | 29 sec integration timeout, 10 MB payload limit |
| S3 | 5 TB object max, multipart required >5 GB, 100 buckets/account default |
| DynamoDB | 400 KB item size max, 1 MB query/scan result page |
| SQS Standard | at-least-once delivery, 256 KB message size, 14 day retention max |
| SQS FIFO | exactly-once, 300 TPS (3,000 with batching) |
| EventBridge | 256 KB event size |
| CloudFront | 30,000 distributions per account |
| Step Functions Standard | 1 year max duration, charged per state transition |
| Step Functions Express | 5 min max duration, charged per execution duration |
| Cognito User Pool | tokens: ID/Access = 1 hr default, Refresh = 30 days default |

### SAP-C02 Mindset Shifts from SAA-C03

1. **Cost is always a constraint** — the "best" answer on SAP often involves cost trade-offs. Know when Spot Instances, Reserved Instances, and Savings Plans apply.
2. **Scale changes everything** — what works at 1,000 users fails at 1,000,000. DynamoDB similarity search in Lambda works for your demo; OpenSearch k-NN is required at 10M photos.
3. **Org and governance appear constantly** — AWS Organizations, SCPs, AWS Control Tower, Service Catalog. These don't exist on SAA-C03.
4. **Migration scenarios** — SAP-C02 tests lift-and-shift vs. re-architect decisions. Know the 6 Rs: Rehost, Replatform, Refactor, Repurchase, Retire, Retain.
5. **You are the architect, not the developer** — SAP questions ask what architecture to recommend, not how to write the code. Think in services and trade-offs.
