# Workflow Tips — Serverless Photo Galleria

Tips for working effectively with Claude on this project.

---

## Debugging Broken UI

**When something "does nothing" on click**
Open DevTools (F12) → Console tab, click the broken button, copy any red errors here.
A `ReferenceError` or `TypeError` tells us exactly what failed before the action ran.

**When something looks visually wrong after a fix**
Right-click the broken element → Inspect → Computed tab.
Look at the suspicious CSS property (e.g. `position`, `opacity`, `transform`).
If it doesn't match what the code sets, a more specific rule is overriding it — paste the computed value and I'll find the conflict immediately.

**When a field "reverts" after saving**
Check the Network tab (F12 → Network) while clicking Save/Apply.
- If the API call never appears → JS error before the fetch (check Console).
- If it returns 200 → the save succeeded; the UI just reloaded stale data too early (timing issue).
- If it returns 4xx/5xx → paste the response body; the Lambda usually explains why it rejected it.

---

## Deploying Changes

**After every deploy, do a hard refresh** (Ctrl+Shift+R) or use an incognito window.
CloudFront caches aggressively. If it still looks wrong, check that the CloudFront invalidation ran — it occasionally fails silently.

**Pipeline changes (category, color mood, tagging) take 30–60 seconds.**
After clicking Apply in the photographer portal, wait before reloading your library.
The `/process` API returns 200 immediately, but the Step Functions pipeline runs asynchronously.

---

## AWS Pipeline Debugging

**When an uploaded photo is missing features** (color mood, GPS, similar photos, tags):
Go to AWS Console → Step Functions → your state machine → find the execution for that photo.
The failed step shows the exact Python exception from the Lambda.
This is much faster than guessing which part of the pipeline broke.

**When bash output looks wrong** (truncated file, wrong line count):
Tell me — I'll switch to the Read tool, which bypasses the Linux mount cache and sees the real Windows file.

---

## Getting Faster Answers from Claude

**If a fix doesn't work the second time**, push back and ask:
*"What would prove to you this is actually the problem?"*
This forces reasoning from evidence rather than guessing.

**Give me console errors before describing the symptom.**
A `ReferenceError: _applyWallSize is not defined` is more actionable than "the wall preview does nothing."

**Tell me if something was already deployed.**
"Deployed and still broken" vs. "not deployed yet" changes everything about where to look.
