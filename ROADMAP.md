# Serverless Photo Galleria — Project Roadmap

**Last updated:** July 2026  
**Developer:** Joel Hammond — [joel@proactivetechsolutions.net](mailto:joel@proactivetechsolutions.net)  
**Program:** Digital Cloud Training (DCT) Collaborative Program

---

## Phase 1 — Foundation ✅ COMPLETE

> Core marketplace built and deployed. Both portals functional across two AWS regions.

### Architecture
- Dual-portal SPA: Photographer Portal (PP) + Customer Portal (CP)
- 37 Lambda functions — Python 3.13, single-responsibility per function
- DynamoDB GlobalTables — 9 tables, active-active replication (us-east-1 ↔ eu-west-1)
- 4 CloudFront distributions — portal SPAs, photo assets, API
- WAFv2 Web ACL on the API distribution with rate limiting + managed rule groups
- Dual Amazon Cognito user pools — physically separate pools for photographers and buyers
- Multi-region SAM template with conditional resource deployment

### AI/ML Pipeline
- 6-stage Step Functions Standard Workflow: ModerateContent → ProcessImage → TagImage → EnrichWithAI → EmbedPhoto → NotifyProcessed
- Amazon Rekognition — content moderation + scene/object labels
- Amazon Bedrock (Claude) — AI-generated titles, descriptions, and keyword tags
- Amazon Titan Embeddings — vector embeddings for semantic similarity search
- Amazon Translate — 49-language i18n baked in at deploy time (static JSON, no runtime cost)

### Photo Features
- 14-parameter photo adjustment panel (exposure, highlights, shadows, brightness, contrast, saturation, vibrance, warmth, tint, sharpness, definition, and more)
- pHash duplicate detection
- GPS/EXIF extraction + map-based browse
- Color mood extraction + palette browse
- Presigned PUT URLs for direct S3 upload (no Lambda proxying)
- Presigned GET URLs for time-limited download delivery

### Commerce & Social
- Stripe checkout + download delivery
- Audio story per photo (voice notes with presigned PUT/GET)
- Photo series / essays
- Follow · Feed · Like
- Collector profiles + collections
- GDPR suite — consent, my-data export, audit log (7-year TTL)

### Observability
- AWS X-Ray: `Tracing: Active` on all 37 Lambdas
- `patch_all()` SDK instrumentation on 11 core functions
- Custom X-Ray annotations (`photoId`, `userId`, `operation`) for business-context filtering
- Step Functions pipeline traced end-to-end as a single connected trace
- EventBridge custom bus: `photo.processed` + `photo.pipeline.failed`

---

## Phase 2 — Stabilise 🔄 IN PROGRESS

> Security hardening, documentation, and cleanup before onboarding a second developer.

### Security Fixes (code done — deploy pending)
- [x] IDOR fix in `DeletePhotoFunction` — ownership check against JWT sub before deletion
- [x] Upload file extension + MIME type allowlist (`upload_url/app.py`)
- [x] Audio content-type allowlist (`audio_story/app.py`)
- [x] CORS restricted to PP CloudFront origin on photographer-only endpoints
- [ ] Deploy security fixes to us-east-1 (`sam build --cached && sam deploy`)
- [ ] Verify eu-west-1 secondary region end-to-end

### Documentation
- [x] Article 1 — Architecture (Medium + LinkedIn)
- [x] Article 2 — Bugs & Lessons (Medium + LinkedIn)
- [x] Article 3 — AI Co-Development (Medium + LinkedIn)
- [x] 4 architecture diagrams (Article 1, Article 2, Article 3, Overall system)
- [ ] Publish all three articles on Medium
- [ ] Publish all three articles on LinkedIn
- [ ] Send Slack update to DCT #may-collaborative-group-2

### Code Cleanup
- [ ] Remove dead Lambda source directories: `src/blur/`, `src/crop/`, `src/compress/`, `src/rotate/` — not referenced in template, add build noise
- [ ] Git commit all pending changes (security fixes, template.yaml ALLOWED_ORIGIN, article updates)
- [ ] Restrict CORS on customer-facing endpoints (currently wildcard `*`)
- [ ] Audit remaining Lambdas for generic error messages (no AWS SDK details in responses)

---

## Phase 3 — Harden 📋 NEXT

> Automated testing, CI/CD, and collaboration infrastructure before the second developer joins.

### Unit Testing (pytest + moto)
Priority functions to cover first:
- `delete_photo` — ownership check (IDOR regression test)
- `upload_url` — file extension and MIME type validation
- `trigger_pipeline` — `_sanitize_adjustments()` input clamping
- `search` — pagination parameter clamping (prevent oversized scans)
- `stripe_webhook` — signature verification

Test strategy: `moto` mocks for DynamoDB and S3; `pytest` with event fixtures mimicking API Gateway payloads. Aim for coverage of security-critical paths and any function that handles user-supplied input.

> **SAA-C03/SAP-C02 alignment:** operational excellence pillar — automated testing as a quality gate.

### CI/CD — GitHub Actions
Workflow triggered on push to `main`:
1. `sam build`
2. Run pytest suite — fail fast on any test failure
3. `sam deploy --config-env default` → us-east-1
4. `sam deploy --config-env euwest1` → eu-west-1 (on primary success)
5. CloudFront invalidation for portal distributions after frontend changes

> **SAP-C02 alignment:** DevOps and deployment automation domain.

### Collaboration Readiness (before second developer joins)
- `CONTRIBUTING.md` — local setup, deploy runbook, PR workflow, environment variable reference
- Git branch protection on `main` — require PR + review before merge
- Feature branch naming convention documented
- Secrets rotation plan for Stripe keys + SSM Parameter Store values

### Additional Security Hardening
- Extend CORS restriction to all customer-portal endpoints (not just photographer routes)
- Audit every Lambda for information leakage in error responses
- Enable CloudTrail + AWS Config for compliance audit trail (maps to SAA-C03 compliance domain)

---

## Phase 4 — Grow 🔭 FUTURE

> New features, scale improvements, and certification milestones.

### Planned Features
- **Polly audio playback** — text-to-speech for photo descriptions as an accessibility layer
- **Print fulfillment integration** — print-on-demand API (Printful or similar) wired to the order flow
- **Photographer analytics dashboard** — views per photo, revenue over time, top-performing work
- **License tiers** — personal, commercial, editorial — priced differently and watermarked accordingly
- **Bulk upload** — multi-file upload with batch Step Functions trigger

### Scale & Resilience
- DynamoDB on-demand → provisioned capacity with auto-scaling (cost optimization at real traffic)
- SQS buffer in front of `TriggerPipelineFunction` to absorb upload bursts
- Route 53 health-check failover between us-east-1 and eu-west-1
- CloudWatch dashboards + on-call runbook

> **SAP-C02 alignment:** cost optimization and reliability domain.

### Certification Milestones
- AWS Solutions Architect Associate (SAA-C03) exam
- AWS Solutions Architect Professional (SAP-C02) exam
- Article 4 — CI/CD and DevOps on AWS (after Phase 3 complete)

### DCT Collaboration
- Onboard second developer using `CONTRIBUTING.md` runbook
- Split feature ownership by domain (e.g., commerce layer vs. discovery/search layer)
- Sprint planning + PR review workflow in place

---

## Immediate Priority List

These items are the most urgent loose ends from Phase 2:

| Priority | Item | Status |
|---|---|---|
| 🔴 | Deploy security fixes → us-east-1 (`sam build && sam deploy`) | Pending |
| 🔴 | Git commit all pending changes | Pending |
| 🔴 | Publish Article 1 on Medium + LinkedIn | Pending |
| 🟡 | Send Slack update to DCT group | Pending |
| 🟡 | Remove dead Lambda directories | Pending |
| 🟡 | Verify eu-west-1 end-to-end | Pending |
| 🟢 | Write pytest unit tests (5 critical Lambdas) | Phase 3 |
| 🟢 | Set up GitHub Actions CI/CD | Phase 3 |
| 🟢 | CONTRIBUTING.md + branch protection | Phase 3 |

---

*This roadmap reflects the state of the project as of July 2026. It will be updated as phases complete and priorities shift.*
