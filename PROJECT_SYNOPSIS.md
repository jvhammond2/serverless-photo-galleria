# Serverless Photo Galleria — Project Synopsis

## What It Is

Serverless Photo Galleria is a full-stack, multi-region photo marketplace built entirely on AWS serverless infrastructure. It consists of two distinct web portals: a **Photographer Studio Portal** where photographers upload, manage, and sell their work, and a **Customer Gallery** where buyers browse, discover, and purchase fine-art prints. The project was built as a portfolio/certification project demonstrating real-world AWS architecture and modern web development practices.

---

## AWS Infrastructure

| Service | Role |
|---|---|
| **AWS SAM** | Infrastructure as Code — defines all resources in `template.yaml` |
| **AWS Lambda** (Python 3.12) | ~30 serverless functions handling all business logic |
| **API Gateway** (REST) | Single API with two Cognito authorizers routing photographer vs. customer requests |
| **DynamoDB Global Tables** | Multi-region NoSQL — Photos, Profiles, Series, Follows, Collections, Likes, Cart |
| **S3** | Original photo storage, thumbnail/preview storage, frontend hosting, translation files |
| **CloudFront** | CDN for both portals and all S3 assets; enforces HTTPS |
| **Cognito** | Two separate User Pools — `PhotographerCognito` and `CustomerCognito` — JWT auth |
| **Step Functions** | Orchestrates the 6-stage photo processing pipeline |
| **EventBridge** | Decouples pipeline success/failure events from downstream consumers |
| **Amazon Rekognition** | AI content moderation — flags explicit or sensitive content before publishing |
| **Amazon Bedrock (Claude)** | AI photo enrichment — generates titles, descriptions, and tags from image content |
| **Amazon Titan Embeddings** | Vector embedding of photos for semantic similarity search |
| **Amazon Translate** | Generates 49-language translations of all UI strings at deploy time |
| **Amazon Polly** | (planned) Text-to-speech for audio story playback |
| **AWS X-Ray** | Distributed tracing across all Lambda functions and API calls |
| **AWS WAF** | Web Application Firewall on the CloudFront distribution |
| **SNS** | Notifications on pipeline failure events |
| **Stripe** | Payment processing for print purchases (via Lambda webhook) |

**Regions:** Primary us-east-1, secondary eu-west-1 (DynamoDB Global Tables replicate automatically).

---

## Photo Processing Pipeline (Step Functions)

Every uploaded photo passes through a 6-stage state machine before appearing in the library:

1. **ModerateContent** — Rekognition scans for explicit content. Flagged photos are quarantined in DynamoDB and the pipeline stops.
2. **ProcessImage** — Pillow generates a thumbnail (400px) and preview (1200px), applies any photographer-requested adjustments (brightness, contrast, saturation, sharpness, blur), and writes all three sizes to S3.
3. **TagImage** — Extracts EXIF/GPS metadata, computes a perceptual hash (pHash) for duplicate detection, identifies dominant color palette and assigns a color mood, and calls Rekognition for scene/object tags.
4. **EnrichWithAI** — Calls Bedrock (Claude) to generate a title, description, and keyword tags from the image.
5. **EmbedPhoto** — Calls Amazon Titan to generate a vector embedding, stored for semantic similarity search.
6. **NotifyProcessed** — Puts a `photo.processed` event on EventBridge; the pipeline emits `photo.pipeline.failed` on any error.

---

## Photographer Studio Portal (`index.html`)

The dark-themed portal photographers use to run their business.

### Upload & Processing
- Drag-and-drop or click-to-select photo upload (JPEG, PNG, WebP, TIFF up to 50 MB)
- Pre-upload adjustment panel: brightness, contrast, saturation, sharpness, blur — applied during pipeline processing
- Category assignment at upload
- Limited Edition toggle — marks prints with edition badge and scarcity counter for buyers
- Real-time upload status with pipeline progress feedback

### My Library
- Grid view of all processed photos with thumbnail previews
- Per-photo lightbox with full adjustment controls, category selector, and AI composition feedback panel
- AI Composition Feedback — Bedrock analyzes framing, exposure, and composition and returns structured critique
- Editor's Choice star toggle — admin/photographer can flag standout photos for featured placement
- Photo story (audio note) recording — attach a voice narrative to any photo; stored in S3 and linked to the photo record

### Series / Photo Essays
- Create named series grouping multiple photos into a narrative sequence
- Add title, description, cover photo, and ordered photo list
- Series visible on the customer portal under the photographer's profile

### My Profile
- Display name, bio, location, website, Instagram link
- Equipment list
- Watermark text — appended to all published images
- Profile saved to DynamoDB and surfaced on the customer portal

### AI Features
- Composition feedback on upload (Bedrock)
- Auto-generated title, description, and tags (Bedrock)
- Color mood auto-detection (tagging Lambda)

### Sales & Admin
- Earnings dashboard — revenue summary and per-photo sales breakdown
- Needs Review queue — flagged/moderated photos awaiting photographer decision
- Analytics panel

---

## Customer Gallery Portal (`customer.html`)

The light-themed public-facing gallery where buyers discover and purchase prints.

### Discovery & Browse
- Full-text search across photo titles, tags, and AI-generated descriptions
- **Category browse** — 30 photographic categories (landscape, nature, street, fashion, etc.) via dropdown
- **Color mood browse** — filter by dominant palette (warm, cool, neutral, vibrant, muted, monochrome)
- **Map browse** — Leaflet.js map showing geotagged photos; click a pin to open the photo
- **Editor's Choice** filter — curated selection of standout images
- **Follow** — follow individual photographers; followed photographers' new work appears in the feed
- **Personalised feed** — chronological feed of photos from followed photographers

### Photo Discovery
- **Semantic similarity search** — "More like this" button on any photo triggers a vector search via Titan embeddings
- **Ambient lightbox** — background of the lightbox shifts to the dominant palette of the open photo
- **Audio story playback** — plays the photographer's voice note attached to a photo
- **Wall preview / print mockup** — interactive room scene lets buyers visualize the print at different sizes on a wall
- **Series browse** — view a photographer's grouped photo essays as a narrative sequence

### Purchase Flow
- Add to cart, checkout via Stripe
- Secure download delivery — time-limited presigned S3 URL generated post-payment
- Purchase history in the account dropdown

### Collector Profile
- My Collection panel shows all purchased prints
- Collector showcase page (public profile for buyers)

### Account & Personalization
- Cognito-authenticated buyer accounts
- Account dropdown: My Collection, Cart, Purchases, Sign Out
- GDPR consent flow, data export (My Data), account deletion

### Internationalisation
- 49 languages — UI strings translated at deploy time via AWS Translate from a master `en.json`
- Language switcher in the nav; selected language persists via localStorage
- Translations served as static JSON from S3/CloudFront (zero per-request cost)

---

## Image Protection

- **Watermark** — photographer's custom text burned into every preview/thumbnail via Pillow, with EXIF/XMP copyright metadata embedded
- **Right-click & drag prevention** on all gallery images in the customer portal
- **pHash fingerprinting** — perceptual hash stored at ingest; similarity search can detect re-uploaded duplicates

---

## Security

- Two isolated Cognito User Pools — photographer JWTs never accepted on customer routes and vice versa
- API Gateway enforces JWT validation before any Lambda is invoked
- WAF on CloudFront distribution
- Presigned S3 URLs for all photo access (no public S3 buckets for originals)
- Time-limited download URLs generated only after confirmed payment

---

## Tech Stack Summary

**Backend:** Python 3.12, AWS SAM, Lambda, API Gateway, DynamoDB, S3, Step Functions, Rekognition, Bedrock, Titan, Translate, EventBridge, Cognito, X-Ray, Stripe, Pillow, imagehash, aws-xray-sdk

**Frontend:** Vanilla HTML/CSS/JS, Amazon Cognito Identity SDK, Leaflet.js (maps), Web Audio API (voice recording), CSS custom properties for theming

**Tooling:** AWS SAM CLI (`sam build` / `sam deploy`), Python `py_compile` for syntax validation, `sam validate` for template validation, GitHub for version control
