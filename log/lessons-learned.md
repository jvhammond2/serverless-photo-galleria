# Lessons Learned — Serverless Photo Galleria

**Project:** Serverless Photo Galleria — multi-region photo marketplace  
**Sessions covered:** 2026-06-13 · 2026-06-16  
**Author:** Joel Vaz (joel@proactivetechsolutions.net)

---

## What This Project Became

What started as a Digital Cloud Training course exercise evolved into a full-stack, production-grade photo marketplace deployed to two AWS regions. The final application includes two distinct portals (Photographer Studio and Customer Gallery), a 6-stage Step Functions upload pipeline, Stripe payment processing, AI-powered photo enrichment via Bedrock, semantic similarity search via Titan Embeddings, distributed tracing via X-Ray, and internationalization across 49 languages.

Going beyond the assignment scope was a deliberate choice — and the right one.

---

## Technical Lessons Learned

### 1. CloudFront Distributions Are Permanent Infrastructure — Know Your URLs

**What happened:** After stacks were re-deployed, the CloudFront distribution URLs changed and existing accounts were no longer valid. Time was lost trying to sign into the wrong portal.

**Lesson:** CloudFront distributions generate a fixed URL at creation time. They don't change unless deleted and recreated. After any `sam deploy`, immediately capture all four distribution URLs from the stack Outputs and update `samconfig.toml` notes and memory accordingly. The "correct" URL is always in `aws cloudformation describe-stacks --query Outputs`.

**Cert note (SAA-C03):** CloudFront distributions are edge-cached CDN endpoints. Each distribution has exactly one domain unless you add a custom CNAME. They survive stack updates as long as the resource isn't replaced.

---

### 2. PP and CP Are Separate S3 Buckets — Never Cross-Upload

**What happened:** `customer.html` was uploaded to the `frontend` (PP) bucket instead of the `purchaser` (CP) bucket. This caused the customer portal series section to show nothing for several sessions before the root cause was identified.

**Lesson:** Two portals = two separate S3 buckets. Always verify the target bucket name before running `aws s3 cp`. The mapping is:
- `index.html` → `serverless-photo-galleria-frontend-us-east-1-*`
- `customer.html` → `serverless-photo-galleria-purchaser-us-east-1-*`

After every upload, invalidate the correct CloudFront distribution.

---

### 3. Cognito SDK Swallows JavaScript Errors — They Surface as Auth Failures

**What happened:** Clicking Sign In on the Photographer Portal showed "Cannot set properties of null (setting 'textContent')". This looked like an auth error but was actually a JavaScript crash inside the `onSuccess` callback.

**Root cause:** The Cognito Identity SDK wraps the `onSuccess` callback in a `try/catch` and routes any exception to `onFailure`. So a JS crash in `onAuthSuccess` appears to the user as a sign-in failure message.

**Fix:** In `onAuthSuccess`, always rebuild the avatar container HTML before trying to set `textContent` on child elements — don't assume the DOM is in a known state after sign-out/sign-in cycles.

**Lesson:** When a Cognito sign-in appears to fail but credentials are correct, check the browser console for a `TypeError` or `ReferenceError` before assuming an auth problem.

---

### 4. Silent `except Exception` Is the Hardest Bug to Find

**What happened:** Every photo uploaded showed `colorMood: "neutral"` regardless of actual colors. No error was logged. No alert appeared.

**Root cause:** The `tagging` Lambda called `s3.get_object()` on the thumbs bucket. The `TaggingFunction` IAM policy was missing `S3ReadPolicy` for the thumbs bucket. S3 threw `AccessDenied`. The `except Exception` block caught it silently and fell through to `color_mood = "neutral"`.

**Fix:** Add `S3ReadPolicy` for the thumbs bucket to `TaggingFunction` in `template.yaml`. Run `sam build && sam deploy`.

**Lesson:** When a feature always returns a default value, suspect a silent exception swallowing an error. Look for `except Exception: pass` or `except Exception: return default` patterns in the relevant Lambda.

**Where X-Ray would have helped:** The S3 GetObject subsegment in the X-Ray trace would have appeared as a **fault** (red) with `AccessDenied` as the error message. This would have identified the IAM gap in 2 minutes instead of requiring code review.

---

### 5. IAM Policies Must Match Every Resource the Lambda Touches

**What happened:** Related to #4. The `TaggingFunction` had `S3ReadPolicy` for the originals bucket but not the thumbs bucket, even though the Lambda reads thumbnails for color extraction.

**Lesson:** When you add new S3 read/write operations to a Lambda, immediately add the matching IAM policy resource. The Lambda won't error at deploy time — it will fail at runtime with `AccessDenied` and only if that code path is exercised.

**Cert note (DVA-C02):** Lambda execution roles use least-privilege IAM. Always verify that each `S3ReadPolicy`, `DynamoDBCrudPolicy`, etc., lists every bucket or table the function actually accesses.

---

### 6. `requirements.txt` Must Exist in Every Lambda Directory

**What happened:** 9 Lambda functions imported `aws_xray_sdk` but had no `requirements.txt`. Python 3.13 runtime does not include it. All 9 functions failed on cold start with `Runtime.ImportModuleError: No module named 'aws_xray_sdk'`.

**Fix:** Create a `requirements.txt` in each function's `CodeUri` directory containing `aws-xray-sdk`. Run `sam build` to bundle it.

**Lesson:** `sam build` only packages dependencies that exist in `requirements.txt` at the function's path. A missing file means the package is never bundled, even if it works locally (because your local Python environment has it installed globally).

---

### 7. CORS Errors Often Mask Real HTTP Errors

**What happened:** API calls returned CORS errors in the browser. The actual problem was 401 Unauthorized from the API Gateway authorizer, but without CORS headers on the 4xx response the browser couldn't read the status code.

**Fix:** Add `GatewayResponseDefault4XX` and `GatewayResponseDefault5XX` resources in `template.yaml` to inject `Access-Control-Allow-Origin` headers on all error responses.

**Lesson:** If a browser reports a CORS error on a call that previously worked, check whether the API is returning a 401/403/502 first. The CORS error is a symptom, not the cause.

**Cert note (SAA-C03/DVA-C02):** API Gateway Gateway Responses let you customize the response body and headers for authorizer failures and integration errors — separately from Lambda response headers.

---

### 8. `position: fixed` Is More Reliable Than Restructuring Flex Layouts

**What happened:** The photo adjustment panel needed to move from below the lightbox image to the side. Multiple attempts to restructure the existing flex layout of the lightbox failed due to accumulated CSS specificity conflicts.

**Fix:** Applied `position: fixed; right: 0; top: 0; width: 420px; height: 100vh` to the adjustment panel, making it a floating right-side drawer independent of the lightbox layout.

**Lesson:** When a UI element needs to break out of a complex existing layout, `position: fixed` avoids fighting accumulated specificity and flex-direction conflicts. The trade-off is that you must explicitly manage `z-index` and close behavior (the panel persists independently of the parent container).

---

### 9. `defer` Script Loading Creates Timing Races

**What happened:** `buildEffectsGrid()` was called inline in the HTML before `i18n.js` (loaded with `defer`) had run. The effects grid rendered with `undefined` labels because the translation strings weren't loaded yet.

**Fix:** Replace the inline call with `document.addEventListener('i18n:ready', buildEffectsGrid)`.

**Lesson:** Any function that depends on a `defer`-loaded script must be called inside an event listener that fires after that script completes — not inline in the HTML.

---

### 10. X-Ray Was There the Whole Time — We Just Didn't Use It

**What happened:** All 11 core Lambdas were instrumented with `patch_all()` and `xray_recorder`. `Tracing: Active` was set globally in the SAM template. The Step Functions pipeline had `Tracing: Enabled: true`. Despite this, debugging was done entirely through code review and log reading.

**Lesson:** Instrumentation has no value if you don't open the console during debugging. When a Lambda fails silently — especially in an async pipeline — the X-Ray Service Map and Traces should be the **first** tool opened, not a last resort. The custom `_annotate()` helpers (tagging traces with `photoId`, `userId`, `operation`) make filtering fast and targeted.

**How to use it next time:**
1. AWS Console → X-Ray → Service Map (identify which node is red/orange)
2. Traces → filter by `annotation.operation` or `annotation.photoId`
3. Expand the faulting subsegment to see the exact exception and AWS SDK call

---

## How We Collaborated — and Where to Improve

### What Worked Well

**Code review over guessing.** When a bug was unclear, reading the actual Lambda code (not guessing from symptoms) almost always found the root cause. Providing console errors or network responses before describing symptoms saved multiple round-trips.

**Screenshots with DevTools open.** Screenshots showing the Network tab (status codes and response bodies) or the Console tab (exact error messages) resolved bugs that description alone would have taken much longer to diagnose. The more specific the error text, the faster the fix.

**Documenting as we went.** Session logs, the WORKFLOW_TIPS file, and this document create an institutional memory that persists across conversations. Without them, context has to be re-established from scratch each session.

**Iterating on UI until confirmed.** The fixed adjustment panel drawer was tried in multiple approaches (flex restructure, then `position: fixed`). Confirming with screenshots before moving on prevented shipping a broken layout.

---

### Where We Could Improve

**Use X-Ray first for Lambda failures.** Every silent failure in this project (colorMood neutral, ImportModuleError, CORS masking 401) could have been identified faster by checking X-Ray traces before reading code. Build this into the debugging habit: always open X-Ray before reading Lambda source.

**Verify the target bucket/URL before every deploy.** The wrong-bucket upload (customer.html → frontend bucket) cost multiple sessions. A pre-deploy checklist — confirm bucket name, confirm distribution ID, confirm file — would eliminate this class of error.

**Check IAM policies when adding new Lambda capabilities.** Every time a Lambda is modified to access a new S3 bucket, DynamoDB table, or AWS service, the IAM policy in `template.yaml` should be updated in the same commit. IAM gaps fail silently at runtime, not at deploy time.

**Run `sam build` before every deploy, not just when code changes.** Stale build artifacts caused confusion in several sessions. `sam build` is fast with `--cached`; there's no reason to skip it.

**Hard refresh after every CloudFront invalidation.** `Ctrl+Shift+R` or incognito window. CloudFront caching is aggressive and a stale cache caused multiple "it's still broken" false positives.

---

## Beyond the Technical — What This Project Actually Taught

The project scope expanded far beyond a course requirement — and that expansion was the real lesson.

**Working with AI tooling changes how you build.** Using Claude as a collaborator throughout meant bugs were diagnosed through a conversation rather than Stack Overflow and trial-and-error. The key skill wasn't knowing what to ask — it was learning to provide the right inputs (error messages, screenshots, deployed vs. not-deployed status) so the diagnosis could be precise. That's a transferable skill for any AI-assisted engineering workflow.

**Diving into unknown tech without fear.** Most of the services used in this project (Bedrock, Titan Embeddings, Rekognition, X-Ray, Step Functions, Stripe webhooks) were not deeply familiar at the start. The approach that worked: read the SAM docs for the resource definition, write the Lambda code, deploy, and debug from the error. The willingness to ship something broken and fix it from evidence is faster than studying until you're confident.

**Stepping up when things aren't going well.** Several sessions hit walls — wrong bucket, broken sign-in, features silently failing. The productive response was to methodically work through each symptom rather than stepping back. When something isn't working, the next step is always evidence gathering: What does the console say? What does the network response say? What does the code actually do?

**Personnel and collaboration lessons.** Managing a project under ambiguity, knowing when to push forward vs. ask for clarification, and communicating progress clearly to others (instructors, peers, stakeholders) are skills built alongside the technical ones. The Slack message, resume content, and Medium article written during this project are evidence of that.

**Documentation is not optional.** Every decision that wasn't written down had to be re-derived the next session. The session logs, WORKFLOW_TIPS, and PROJECT_SYNOPSIS are now the fastest path back into context. That habit — write it down while it's clear — transfers directly to professional engineering work.

---

## AWS Cert Reference (SAA-C03 / DVA-C02)

| Topic | What Was Learned |
|-------|-----------------|
| **Gateway Responses** | API Gateway lets you customize 4xx/5xx error responses — including adding CORS headers — so browsers see real HTTP status codes instead of CORS failures |
| **Lambda ImportModuleError** | `Runtime.ImportModuleError` at cold start = missing `requirements.txt` in the function's CodeUri directory |
| **`defer` script loading** | Deferred scripts run after HTML parsing; inline JS that depends on them must use an event listener |
| **IAM least-privilege** | Lambda execution roles need explicit `S3ReadPolicy` / `DynamoDBCrudPolicy` for every resource accessed |
| **X-Ray sampling** | X-Ray does not trace 100% of requests by default — sampling rules in the console control coverage vs. cost |
| **X-Ray segments vs. subsegments** | Segments = one per service boundary (Lambda, API GW). Subsegments = one per SDK call within a segment (DynamoDB GetItem, S3 GetObject) |
| **Step Functions tracing** | `Tracing: Enabled: true` on a state machine makes the entire pipeline visible as a single end-to-end X-Ray trace |
| **DynamoDB Global Tables** | Multi-region replication with eventual consistency. Primary region writes; replicas in secondary regions are read-optimized |
| **CloudFront invalidation** | Invalidations force edge locations to re-fetch from origin. Without them, cached responses serve stale content after a deploy |
| **Cognito authorizers** | API Gateway Cognito authorizers validate the JWT signature and expiry before the Lambda is ever invoked. A 401 from the authorizer never reaches the Lambda |
