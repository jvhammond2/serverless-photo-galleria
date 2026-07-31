# The Bugs That Taught Me More About AWS Than Any Course — Part 2: Hard Lessons

*Part 2 of a series on building a serverless photography marketplace on AWS. Part 1 covered the architecture. This article is about what broke — and what each failure actually taught me.*

---

Every course on AWS shows you the happy path. You provision a Lambda function, wire it to API Gateway, watch the 200 OK roll in, and move on. What courses don't show you is what happens when a Lambda fails silently with no error in the logs, or when a bug in a JavaScript callback disguises itself as a Cognito authentication failure, or when a security vulnerability sits undetected in code that otherwise works perfectly.

Building Galleria meant hitting all of those. Here are the five bugs that taught me the most — not the ones that were hardest to write, but the ones that changed how I think about AWS.

---

## Bug 1: The Feature That Always Returned the Wrong Answer (With No Error Anywhere)

Every photo uploaded to Galleria goes through a six-stage Step Functions pipeline. One stage — `TagImage` — extracts GPS coordinates, computes a perceptual hash for duplicate detection, identifies the dominant color palette, and assigns a color mood (warm, cool, neutral, dark, and so on). That color mood drives a visual browse feature on the customer portal.

For weeks, every photo returned `colorMood: "neutral"`. Didn't matter what was in the photo — golden-hour landscape, blue-sky seascape, all neutral. No error in CloudWatch. No exception in the Lambda logs. Just the wrong answer, quietly, every single time.

The root cause was an IAM policy gap. The `TaggingFunction` Lambda had `S3ReadPolicy` for the originals bucket — the raw upload — but not for the thumbnails bucket, where the processed image it needed for color extraction lived. S3 returned `AccessDenied`. A bare `except Exception` block caught it silently and fell through to the default value.

```python
try:
    response = s3.get_object(Bucket=thumbs_bucket, Key=thumbnail_key)
    # ... color extraction ...
except Exception:
    color_mood = "neutral"  # swallowed AccessDenied, returned default
```

**The fix:** Add `S3ReadPolicy` for the thumbnails bucket to `TaggingFunction` in the SAM template. One line. `sam build && sam deploy`.

**What this taught me:** IAM gaps don't fail at deploy time. They fail silently at the exact moment a specific code path runs on a specific resource. The Lambda's execution role was correct for most of what it did — just not this one operation on this one bucket. When a feature consistently returns a default value, the first question should be: *is there a silent exception swallowing an error somewhere in this path?*

**X-Ray note:** This is exactly the scenario X-Ray is built for. The `S3:GetObject` call would have appeared as a fault — a red subsegment — in the trace, with `AccessDenied` as the error reason. I would have found it in two minutes. Instead I found it by reading the code. I now open X-Ray before reading source.

---

## Bug 2: The Sign-In That Wasn't Failing (It Was Crashing)

The Photographer Portal sign-in stopped working after a UI update. Clicking Sign In with valid credentials triggered the error handler and displayed "Authentication failed." The credentials were correct. Cognito was returning a successful JWT. Nothing in the Cognito logs indicated a problem.

The actual error was in JavaScript, not AWS. The `onSuccess` callback in the Cognito Identity SDK looked like this:

```javascript
cognitoUser.authenticateUser(authDetails, {
    onSuccess: function(result) {
        document.getElementById('user-name').textContent = result.getIdToken()
            .payload['cognito:username'];
        // ...
    },
    onFailure: function(err) {
        showError(err.message);
    }
});
```

The `onSuccess` callback crashed with a `TypeError` because `document.getElementById('user-name')` returned null — the DOM element had been removed during the UI update. The Cognito SDK wraps `onSuccess` in a `try/catch` and routes any exception to `onFailure`. So a JavaScript crash in the success handler appeared to the user as an authentication failure.

**The fix:** Rebuild the relevant DOM elements inside `onSuccess` before trying to access their children. Don't assume the DOM is in a known state after sign-out/sign-in cycles.

**What this taught me:** When Cognito sign-in appears to fail but credentials are correct, check the browser console for a `TypeError` or `ReferenceError` before touching AWS. The SDK's error routing is correct behavior — it's preventing an unhandled exception from crashing the page — but it obscures the real failure. The symptom and the cause are in completely different layers.

---

## Bug 3: CORS Errors That Were Lying About Everything

Early in the project, API calls started returning CORS errors in the browser. This is a common early-stage AWS problem, so the instinct was to start adding `Access-Control-Allow-Origin` headers everywhere. That instinct was wrong.

The actual responses were `401 Unauthorized` from the Cognito authorizer — the JWT had expired and the frontend wasn't refreshing it correctly. But without CORS headers on the error response, the browser couldn't read the HTTP status code. It could only see that the response was blocked. So it reported a CORS error.

The real fix was two things: refresh the token correctly in the frontend, and add proper error response handling in the SAM template:

```yaml
GalleriaApi:
  Type: AWS::Serverless::Api
  Properties:
    GatewayResponses:
      DEFAULT_4XX:
        ResponseParameters:
          Headers:
            Access-Control-Allow-Origin: "'*'"
      DEFAULT_5XX:
        ResponseParameters:
          Headers:
            Access-Control-Allow-Origin: "'*'"
```

`GatewayResponseDefault4XX` and `GatewayResponseDefault5XX` inject CORS headers on authorizer failures and integration errors — responses that never reach your Lambda function and therefore never get your Lambda's CORS headers.

**What this taught me:** When a browser reports a CORS error on a call that previously worked, the actual problem is almost certainly a non-200 HTTP response, not a missing header. CORS is a symptom in this scenario, not the cause. Check the network tab for the real status code before touching CORS configuration.

**Cert note (SAA-C03):** Gateway Responses are a distinct configuration surface from Lambda response headers. Authorizer failures (401, 403) and integration timeouts (504) bypass Lambda entirely — they need CORS headers added at the API Gateway level through Gateway Responses, not inside your Lambda code.

---

## Bug 4: Cold Start Failure Across Nine Lambda Functions

After adding X-Ray instrumentation to the core Lambda functions, nine of them started failing on cold start with the same error:

```
Runtime.ImportModuleError: Unable to import module 'app': No module named 'aws_xray_sdk'
```

The import worked locally. `aws_xray_sdk` was installed in the local Python environment. SAM built without errors. The Lambdas deployed. And then they failed on every cold start.

The problem was missing `requirements.txt` files. When SAM builds a Python Lambda function, it looks for a `requirements.txt` in the function's `CodeUri` directory. If the file doesn't exist, the function gets deployed with only the standard library — no third-party packages. `aws_xray_sdk` was available locally because it was installed globally in the development environment, not because it was bundled into the deployment package.

The fix was creating a `requirements.txt` containing `aws-xray-sdk` in each function's source directory, then running `sam build` again to bundle the dependency.

**What this taught me:** SAM's build process is explicit about dependencies — what isn't in `requirements.txt` doesn't exist in the Lambda runtime, even if it's installed on your development machine. This is a good thing: it makes the deployment artifact deterministic and prevents "it works on my machine" failures. But it means you need to verify `requirements.txt` exists for every function that has third-party imports, not just the ones you're actively modifying.

---

## Bug 5: The Security Hole That Only Showed Up in a Code Review

This one wasn't a crash or a wrong answer. It was a vulnerability that worked perfectly and was completely wrong.

The `DeletePhotoFunction` Lambda handles the endpoint that lets photographers delete their photos. It was correctly protected by the Cognito authorizer — only authenticated photographers could call it. It correctly fetched the photo record from DynamoDB. It correctly deleted the S3 objects and the DynamoDB record.

What it didn't do was check that the requesting photographer actually owned the photo being deleted.

```python
# Original — missing ownership check
def handler(event, context):
    body = json.loads(event.get("body") or "{}")
    photo_id = body.get("photoId")

    item = table.get_item(Key={"photoId": photo_id})["Item"]
    # item["photographerId"] is never checked against the JWT sub

    s3.delete_object(Bucket=ORIGINALS_BUCKET, Key=item["originalKey"])
    table.delete_item(Key={"photoId": photo_id})
```

Any authenticated photographer could delete any other photographer's photos by knowing or guessing a `photoId`. This is an Insecure Direct Object Reference (IDOR) — one of the most common API vulnerabilities, and one that authentication alone doesn't prevent.

The fix was straightforward:

```python
requesting_photographer = event["requestContext"]["authorizer"]["claims"]["sub"]
if item.get("photographerId") != requesting_photographer:
    return {"statusCode": 403, "body": json.dumps({"error": "Forbidden"})}
```

**What this taught me:** Authentication and authorization are different things. Cognito verifies *who you are*. Your application code must verify *what you're allowed to do*. A Cognito authorizer on an API route answers "is this a valid photographer?" It does not answer "does this photographer own this specific resource?" That check has to happen in the Lambda.

This kind of vulnerability is easy to miss because the feature works — it deletes photos, it requires authentication, it returns correct responses. It only becomes a problem when you ask: *what happens if a malicious actor calls this endpoint with someone else's photoId?*

---

## The Pattern Behind All Five

Looking back at these failures, a pattern emerges. Four of the five had the same root structure: something worked at the surface (function deployed, call succeeded, no crash), while a gap at a lower layer meant the behavior was wrong, slow, or unsafe.

Silent exceptions hide IAM gaps. SDK callback routing hides JavaScript crashes. CORS error messages hide HTTP status codes. Local environments hide missing dependencies. Passing tests hide authorization gaps.

The discipline that catches all of them is the same: go one layer deeper than the symptom. Don't accept "it looks like a CORS problem" — find out what the actual HTTP response is. Don't accept "Cognito is failing" — check whether Cognito actually returned a success. Don't accept "the feature works" — ask what happens when someone calls it incorrectly.

That habit — looking past the symptom to the actual failure mode — is the one thing no certification exam will give you. You get it from shipping code that breaks.

---

*Part 3 of this series explores what it actually means to build a production application with AI as a co-developer — what works, what doesn't, and what it changes about how you learn.*

If you're studying for AWS certifications or building your first serverless project, the full source code is on GitHub at [github.com/jvhammond2/serverless-photo-galleria](https://github.com/jvhammond2/serverless-photo-galleria).

---

*Joel is a cloud developer and AWS Solutions Architect candidate building production-grade serverless applications. He is a selected developer on the Digital Cloud Training collaborative program.*
