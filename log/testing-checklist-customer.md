# Galleria Customer Portal — QA Testing Checklist

**Customer portal (us-east-1):** https://d1zup6wc9h9uwc.cloudfront.net  
**Customer portal (eu-west-1):** https://d13n3p9pdofbbh.cloudfront.net  

> Tip: Have at least 2–3 photos already processed and visible in the gallery (from photographer testing) before running these checks.

---

## 1. Authentication

- [ ] Open the customer portal as a guest (not signed in) — the gallery feed should be visible but purchase/like/follow should be gated
- [ ] Click **Sign In** / **Sign Up** — confirm the Cognito-hosted UI or inline form loads
- [ ] Sign up with a new email address (different from the photographer account)
- [ ] Confirm the verification email arrives and the code works
- [ ] Sign in with the confirmed account — confirm the UI updates (name shown, sign-out option appears)
- [ ] Sign out — confirm the session clears and the portal returns to the guest state
- [ ] Try signing in with a wrong password — expect a clear error message, not a blank screen or infinite spinner
- [ ] Refresh the page while signed in — confirm the session persists (token stored correctly)

---

## 2. Photo Feed / Gallery Browse

- [ ] The main feed shows thumbnails of processed photos uploaded by the photographer
- [ ] Thumbnails load quickly (served from CloudFront → thumbs S3 bucket, not originals)
- [ ] Scroll down to trigger pagination or infinite scroll if implemented — confirm more photos load
- [ ] Confirm the watermark is visible on thumbnails (customers should not see clean images without purchasing)
- [ ] Open the page with no photos uploaded yet — confirm an empty state message appears instead of a broken layout

---

## 3. Search & Filter

- [ ] Search by keyword (try a filename or category like "landscape") — confirm results narrow down
- [ ] Search for something that doesn't exist — confirm an empty results message appears
- [ ] Filter by category (e.g., **Landscape**, **Portrait**) — confirm only photos with that category appear
- [ ] Clear the filter — confirm all photos return
- [ ] Combine a search term with a category filter — confirm both constraints apply

---

## 4. Photo Detail / Lightbox

- [ ] Click a thumbnail to open the photo in detail view or lightbox
- [ ] Confirm the full-size **preview** (watermarked) loads — not the original
- [ ] Confirm the photographer's name/handle is shown
- [ ] Confirm the photo title or filename is shown
- [ ] Confirm the price is displayed
- [ ] If Limited Edition: confirm the edition count/availability is shown (e.g., "3 of 10 remaining")
- [ ] Close the lightbox — confirm the gallery is still in the same scroll position

---

## 5. Like / Favourite

- [ ] Click the ❤️ / like button on a photo while signed in — confirm it toggles to liked state
- [ ] Refresh the page — confirm the liked state persists
- [ ] Click the like button again — confirm it un-likes
- [ ] Try liking while signed out — confirm a sign-in prompt appears instead of silently failing
- [ ] Confirm the like count increments/decrements visibly

---

## 6. Follow a Photographer

- [ ] Open a photographer's profile or click their name on a photo
- [ ] Click **Follow** — confirm the button state changes to "Following"
- [ ] Refresh the page — confirm the follow state persists
- [ ] Click **Unfollow** — confirm the state reverts
- [ ] Try following while signed out — confirm a sign-in prompt appears

---

## 7. Photographer Profile Page

- [ ] Navigate to a photographer's profile (from a photo or a direct link)
- [ ] Confirm their display name, bio (if set), and portfolio photos appear
- [ ] Confirm the follower count is visible
- [ ] Photos on the profile are clickable and open the lightbox

---

## 8. Purchase Flow

> You'll need a payment method configured. Test with a small/low-price listing first.

- [ ] Click **Buy** or **Purchase** on a photo while signed in
- [ ] Confirm the price and photo details are shown in the checkout/confirmation dialog
- [ ] Complete the purchase (use a test card if Stripe test mode is active: `4242 4242 4242 4242`, any future date, any CVC)
- [ ] Confirm a success message appears after payment
- [ ] Confirm a confirmation email arrives at the customer's address
- [ ] Check the **My Purchases** or **Downloads** section — the photo should appear there

---

## 9. Download After Purchase

- [ ] Go to **My Purchases** / order history
- [ ] Click **Download** on a purchased photo
- [ ] Confirm the download link is a pre-signed S3 URL (check the URL in the browser) — it should expire after a short time
- [ ] Confirm the downloaded file is the **original** (no watermark), full resolution
- [ ] Try downloading again after the link expires — confirm a fresh pre-signed URL is generated (not a 403)

---

## 10. Limited Edition Enforcement

- [ ] Upload a Limited Edition photo from the photographer portal (set edition size, e.g., 2)
- [ ] Purchase it as Customer A
- [ ] Purchase it as Customer B
- [ ] Try purchasing as Customer C — confirm the purchase is blocked with an "edition sold out" or similar message
- [ ] Confirm the photographer's dashboard reflects the sold count

---

## 11. Notifications / Email

- [ ] Purchase a photo — confirm the customer receives a receipt email
- [ ] Confirm the photographer receives a sale notification email (if implemented)
- [ ] If a photographer you follow uploads a new photo, confirm you receive a notification (if implemented)

---

## 12. Multi-Region

- [ ] Repeat sections 2–4 on the **eu-west-1 customer portal** (https://d13n3p9pdofbbh.cloudfront.net)
- [ ] Confirm photos uploaded via the eu-west-1 photographer portal are visible here
- [ ] Confirm like/follow actions work correctly in the EU region

---

## 13. Performance & UX

- [ ] Open DevTools → Network tab, load the feed — confirm thumbnails are served from CloudFront (check response headers for `x-cache: Hit from cloudfront`)
- [ ] On a slow connection (throttle to "Fast 3G" in DevTools), confirm thumbnails load progressively and there's no blank screen
- [ ] Open the portal on a mobile screen size (or DevTools responsive mode at ~390px wide) — confirm layout is usable, no horizontal scroll
- [ ] Check for console errors (F12 → Console) on the feed page, lightbox, and profile page — should be zero errors

---

## 14. Security Spot-Checks

- [ ] While signed out, manually call the download endpoint with a guessed photo key — confirm you get a 401 or 403, not the file
- [ ] While signed in as Customer A, try to access Customer B's order history (change the ID in the URL) — confirm you get a 403
- [ ] Confirm the preview images (watermarked) are publicly accessible — this is intentional for the gallery
- [ ] Confirm the originals S3 bucket is **not** publicly accessible (try a direct S3 URL — should return 403 or Access Denied)

---

## 15. Post-Testing Notes

- [ ] Log any bugs found in `log/2026-06-13.md`
- [ ] Note which checks passed on eu-west-1 vs us-east-1
- [ ] If purchase flow isn't wired up yet, mark section 8–10 as deferred and note it
