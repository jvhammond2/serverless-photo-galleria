# Galleria QA Testing Checklist

Test against both portals unless marked (primary only).

**Photographer portal (us-east-1):** https://dps7ixpjebpsu.cloudfront.net  
**Photographer portal (eu-west-1):** https://d39naeqp4sb0y6.cloudfront.net  
**Customer portal (us-east-1):** https://d1zup6wc9h9uwc.cloudfront.net  
**Customer portal (eu-west-1):** https://d13n3p9pdofbbh.cloudfront.net  

---

## 1. Authentication

- [ ] Sign up as a new photographer (use a real email — Cognito sends a verification code)
- [ ] Confirm the verification email arrives and the code works
- [ ] Sign in with the confirmed account
- [ ] Sign out and confirm the portal returns to the unauthenticated state
- [ ] Try signing in with a wrong password — expect a clear error, not a blank screen

---

## 2. Photo Upload — Basic Flow

- [ ] Click **Upload**, choose a JPEG from your computer
- [ ] Confirm the presigned URL request succeeds (no CORS error in browser DevTools → Network tab)
- [ ] Confirm the S3 PUT returns 200
- [ ] Confirm the `/process` call returns 200 with an `executionArn`
- [ ] Wait ~15–30 seconds, refresh the gallery — the processed photo should appear with a watermark

---

## 3. Adjustment Panel — Light Group

Test each slider individually. Set it to a non-zero value, upload a photo, and compare the result.

- [ ] **Exposure +50** — image should look noticeably brighter (~1 stop)
- [ ] **Exposure -50** — image should look darker (~1 stop)
- [ ] **Brilliance +60** — shadows lift, highlights pull back, slight colour pop
- [ ] **Highlights -60** — bright sky/clouds should lose blown-out look
- [ ] **Shadows +60** — dark areas (under trees, indoor corners) should open up
- [ ] **Brightness +40** — overall lighter, more linear than Exposure
- [ ] **Contrast +50** — punchy; darks darker, lights lighter
- [ ] **Contrast -50** — flat/faded look
- [ ] **Black Point +40** — shadow floor lifts, matte/faded look (intentional)

---

## 4. Adjustment Panel — Color Group

- [ ] **Saturation +60** — colours pop, should look vivid but not posterized
- [ ] **Saturation -100** — full greyscale (same as B&W preset)
- [ ] **Vibrance +60** — muted colours boost; already-saturated colours stay calm
- [ ] **Warmth +60** — image shifts orange/yellow (golden hour feel)
- [ ] **Warmth -60** — image shifts blue/cool (overcast feel)
- [ ] **Tint +40** — slight green shift
- [ ] **Tint -40** — slight magenta shift

---

## 5. Adjustment Panel — Detail Group

- [ ] **Sharpness +60** — edges sharper; check a portrait for skin texture
- [ ] **Definition +60** — local contrast boost; midtone detail pops without halos
- [ ] **Noise Reduction +50** — smooth out a high-ISO or low-light photo; slight softness expected

---

## 6. Adjustment Panel — Effects Group

- [ ] **Vignette +50** — dark oval border around the image
- [ ] **Vignette +100** — very heavy vignette, corners near-black

---

## 7. Filter Presets

- [ ] Select **Cinematic** — contrast and saturation boost, highlights slightly pulled
- [ ] Select **Golden Hour** — warm, slightly bright, saturated
- [ ] Select **Moody** — dark, desaturated, high contrast
- [ ] Select **Vivid** — punchy colours and definition
- [ ] Select **Matte** — faded/lifted shadows, less saturated
- [ ] Select **B&W** — full greyscale
- [ ] Select **Vintage** — warm, desaturated, vignette visible
- [ ] Select **Portrait** — subtle sharpness + warmth + noise reduction
- [ ] Apply a preset, then manually move one slider — confirm the preset pill deselects
- [ ] Apply a preset, then hit **Reset All** — confirm all sliders return to 0 and "None" activates

---

## 8. Watermark

- [ ] Upload a photo **with no adjustments** — watermark "© Galleria" should appear (bottom-right or similar)
- [ ] Upload with **Vignette +50** — watermark should still be clearly visible on top of the vignette
- [ ] Upload with **Saturation -100** (B&W) — confirm watermark text is readable on a greyscale image
- [ ] (Optional) Set a custom watermark text in your photographer profile, then upload — confirm the custom text appears instead of "© Galleria"

---

## 9. Lightbox & Re-process

- [ ] Click an existing photo to open the lightbox
- [ ] Click **Re-process** (or the edit icon) — the adjustment panel should slide in below the image
- [ ] Set **Exposure +30** and **Vignette +40**, click **Apply**
- [ ] Confirm the status shows "Processing…" then "Done" (or similar)
- [ ] Close and reopen the lightbox — confirm the updated version of the photo is shown
- [ ] Re-process the same photo with **Reset All** (no adjustments) — confirm it reverts to a clean watermarked image
- [ ] Click **Cancel** mid-reprocess setup — confirm no network request fires and the panel closes

---

## 10. Categories

- [ ] Upload a photo and assign the **Landscape** category
- [ ] Upload another and assign **Portrait**
- [ ] Confirm both appear in the gallery (category filtering, if exposed in the UI)

---

## 11. Limited Edition

- [ ] Toggle **Limited Edition** on before uploading a photo
- [ ] Confirm the upload succeeds and the photo is marked as limited edition in the gallery

---

## 12. Step Functions Verification (AWS Console)

- [ ] In the AWS Console → Step Functions → State machines, find the Galleria state machine
- [ ] Click on a recent execution and confirm it reached **Succeeded** status
- [ ] Click into the execution to see the input — confirm `adjustments` array matches what you set in the UI
- [ ] Find a B&W upload and confirm `{"id":"saturation","value":-100}` appears in the execution input

---

## 13. Customer Portal

- [ ] Sign up / sign in as a customer (different email from the photographer account)
- [ ] Browse the photo feed — confirm photos uploaded by the photographer appear
- [ ] Click a photo to open it — confirm a watermarked preview loads
- [ ] Like a photo — confirm the heart/count updates
- [ ] Follow the photographer — confirm follow state persists on page refresh
- [ ] Search for a photo by keyword or category

---

## 14. Multi-Region

- [ ] Repeat a basic upload test on the **eu-west-1 photographer portal** (https://d39naeqp4sb0y6.cloudfront.net)
- [ ] Confirm the photo processes end-to-end in the EU region (check eu-west-1 Step Functions in console)
- [ ] Open the **eu-west-1 customer portal** and confirm photos are visible

---

## 15. Error Handling & Edge Cases

- [ ] Upload a **PNG** (not just JPEG) — should process without error
- [ ] Upload a very large file (10–20 MB JPEG) — confirm it completes without timeout
- [ ] Upload with **every slider at 0** (no adjustments) — confirm pipeline still runs and watermark appears
- [ ] Disconnect from the internet mid-upload — confirm a user-visible error appears (not a blank crash)
- [ ] Open DevTools → Network tab, upload a photo, confirm there are no unexpected 4xx/5xx responses

---

## 16. Post-Testing

- [ ] Commit all working code: `git add -A && git commit -m "feat: modern adjustment panel with 15 parameterized effects"`
- [ ] Tag the release: `git tag v0.5.0-adj-panel`
- [ ] Update `log/2026-06-13.md` with test results
- [ ] Tear down stacks when done reviewing (saves cost): `sam delete --config-env default` and `sam delete --config-env euwest1`
