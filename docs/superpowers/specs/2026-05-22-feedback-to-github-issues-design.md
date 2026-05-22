# Feedback → GitHub Issues — Design

**Date:** 2026-05-22
**Status:** Draft
**Owner:** Frederick
**Context:** Hackathon ended; this lands in the production deployment. Treat tokens, abuse protection, and observability accordingly.

## Goal

Let any visitor submit feedback, ideas, bug reports, or "wrong data about this paper" notes from the SepsisAtlas web app. Submissions become labeled GitHub issues in this repository. No paid third-party service.

## Non-goals

- Authenticated submissions (anonymous is fine)
- Editing or deleting after submit
- Long-term persistence in the app DB — GitHub issues *is* the store
- CAPTCHA / advanced anti-abuse (revisit only if spam appears)

## Approach

A small Next.js API route accepts feedback payloads and creates a labeled issue via the GitHub REST API using a server-only fine-grained PAT. A dedicated `/feedback` page hosts the form; lightweight "Report" buttons elsewhere in the UI deep-link to that page with context pre-filled via query params.

### Why not prefilled `issues/new` URLs?

The redirect-to-GitHub approach is simpler but requires every submitter to have a GitHub account. SepsisAtlas is aimed at clinicians and researchers reviewing extraction quality — many will not. Server-side issue creation keeps the bar at "type into a textbox".

## Architecture

```
User → FeedbackForm (client)
         ↓ POST JSON
       /api/feedback (server)
         → validate (zod)
         → honeypot check
         → per-IP rate-limit
         → lib/github.createFeedbackIssue()
           → GitHub REST: POST /repos/{owner}/{repo}/issues
         ← 201 { issueUrl }
       ← respond { ok: true, issueUrl }
Form → success state with link to issue
```

## Components

| Path | Purpose |
|------|---------|
| `web/app/(chrome)/feedback/page.tsx` | Server component, renders form, reads `?type=&paper=&row=` query for prefill |
| `web/app/api/feedback/route.ts` | POST handler: validate, guard, dispatch |
| `web/src/components/FeedbackForm.tsx` | Client component, controlled form, success/error states |
| `web/src/components/FeedbackButton.tsx` | Small link/button building `/feedback?type=...&paper=...&row=...` |
| `web/src/lib/github.ts` | `createFeedbackIssue(payload)` — wraps GitHub REST API |
| `web/src/lib/feedback-schema.ts` | Shared zod schema (form + route validate against same shape) |
| `web/src/lib/rate-limit.ts` | Simple in-memory sliding-window limiter |

## Data shape

```ts
type FeedbackType = "bug" | "wrong-data" | "idea" | "other";

interface FeedbackPayload {
  type: FeedbackType;
  title: string;          // 5..120 chars
  body: string;           // 10..5000 chars
  paperStem?: string;     // present iff type=wrong-data or contextual
  rowContext?: unknown;   // optional JSON snippet from evidence table
  contact?: string;       // optional email, validated if present
  website: string;        // honeypot — MUST be empty
}
```

## Issue formatting & tagging

Every submitted issue is tagged so maintainers can triage by `gh issue list --label ...` or repo filters.

**Title format:** `[feedback:<type>] <user title>` — keeps GitHub search clean and lets us spot website-origin issues at a glance.

**Required labels on every submitted issue:**

| Label | Meaning |
|-------|---------|
| `feedback` | Origin marker — anything submitted via the website |
| `feedback:bug` / `feedback:wrong-data` / `feedback:idea` / `feedback:other` | Submission type (exactly one) |
| `from-website` | Distinguishes from manually-filed issues |

**Conditional labels:**

| Label | Added when |
|-------|------------|
| `paper:<stem>` | Submission references a specific paper (created lazily; see Setup) |
| `needs-triage` | Always added on creation; maintainer removes once reviewed |

Labels are managed in `web/src/lib/github.ts` so the list is one place to edit. If a `paper:<stem>` label does not exist, the route creates it on demand (`POST /repos/{owner}/{repo}/labels`) with a neutral color, ignoring 422 "already exists" responses.

**Body template:**
  ```
  **Type:** <type>
  **Paper:** <stem or "n/a">
  **Row context:**
  ```json
  <pretty JSON or "n/a">
  ```
  **Contact:** <email or "anon">
  **Submitted:** <ISO timestamp>

  ---

  <user body, untouched>

  ---
  *Submitted via SepsisAtlas feedback form. No IP or user-agent stored.*
  ```

Labels missing in the repo are created on first use (handled by the GH API call, or a one-time setup step — see Setup below).

## Entry points in the UI

1. **Global chrome:** "Feedback" link in `web/app/(chrome)/layout.tsx` header, points to `/feedback`.
2. **Paper detail page (`/papers/[stem]`):** "Report issue with this paper" button → `/feedback?type=wrong-data&paper=<stem>`.
3. **Evidence table rows:** Small icon button on each row → `/feedback?type=wrong-data&paper=<stem>&row=<base64-json>`. The row JSON gets decoded server-side on the feedback page and shown to the user before submission so they can confirm what's being shared.

## Environment variables

Server-only (never exposed to client):

- `GITHUB_FEEDBACK_TOKEN` — fine-grained PAT scoped to `issues:write` on this repo only
- `GITHUB_FEEDBACK_REPO` — e.g. `OrFrederick/SepsisAtlas`. Configurable so the dev/preview deploys can point at a throwaway test repo

Documented in `web/.env.example` (create if absent). The route returns a clean error if either is missing rather than 500-ing.

## Spam controls (prod)

- **Honeypot field** `website` — hidden via CSS, server rejects with 400 if non-empty
- **Per-IP rate-limit:** 5 submissions per rolling hour, in-memory `Map<ip, timestamps[]>` keyed by `x-forwarded-for` (first value) or remote addr. Sliding-window check, prune on each request. Acceptable for current single-instance Next.js standalone deploy; if we ever scale horizontally, swap implementation behind the same interface for Redis or upstash (free tier)
- **Size caps:** title 5–120, body 10–5000, total payload < 16 KB
- **Time-to-fill check:** form records mount timestamp client-side; server rejects submissions completed in under 3 seconds (bots tend to fire instantly)
- **Origin check:** route rejects if `Origin` / `Referer` does not match the deployed app's host (configurable via `FEEDBACK_ALLOWED_ORIGIN`)
- **hCaptcha-ready interface:** `lib/captcha.ts` stub exports `verify(token): Promise<boolean>` returning `true` unless `HCAPTCHA_SECRET` is set. When the env var is present, the form mounts the hCaptcha widget and the route calls hCaptcha's `siteverify`. No code changes needed to flip it on — only env + a widget site key

If post-launch spam slips past honeypot + rate-limit, set the hCaptcha env vars and redeploy. No CAPTCHA at launch.

## Error responses

| Status | When | Body |
|--------|------|------|
| 200 | Issue created | `{ ok: true, issueUrl }` |
| 400 | Validation or honeypot fail | `{ ok: false, error: "invalid" }` (generic, no leak) |
| 429 | Rate limit | `{ ok: false, error: "rate-limited", retryAfterSec }` |
| 502 | GitHub API failure | `{ ok: false, error: "upstream" }` — UI shows fallback (copy text + suggest emailing maintainer) |
| 500 | Misconfigured env | logged server-side, returns 502 to client |

All server-side errors logged with a request id; tokens never logged.

## Testing

**Unit**

- `lib/feedback-schema.ts` — accepts valid payloads, rejects malformed (missing title, too short body, bad email, filled honeypot)
- `lib/github.ts` — given a payload, produces the expected GitHub API request shape (URL, headers, body, labels, title prefix). Use fetch mock.
- `lib/rate-limit.ts` — admits N within window, blocks N+1, expires correctly after window

**Route**

- `app/api/feedback/route.ts` — happy path (mocked GH returns 201), honeypot reject, validation reject, rate-limit trip, GH 5xx → 502

**Component**

- `FeedbackForm` — required-field enforcement, success state shows issue link, error state shows fallback copy

**Manual**

- Submit one of each type against a throwaway test repo configured via `GITHUB_FEEDBACK_REPO`. Confirm labels created, body renders correctly on GitHub.

## Setup steps (one-time, outside this PR)

1. Create fine-grained PAT scoped to repo `OrFrederick/SepsisAtlas`, permission `Issues: read & write` and `Metadata: read`. Set expiry to 1 year; calendar reminder to rotate.
2. Add to prod env (and to `.github/workflows/deploy.yml` secret list):
   - `GITHUB_FEEDBACK_TOKEN` — the PAT
   - `GITHUB_FEEDBACK_REPO` — `OrFrederick/SepsisAtlas`
   - `FEEDBACK_ALLOWED_ORIGIN` — `https://<prod-host>` (comma-separated if multiple)
3. Pre-create labels with deliberate colors (script `scripts/setup-feedback-labels.sh` shipped in PR):
   - `feedback` (gray), `from-website` (gray), `needs-triage` (yellow)
   - `feedback:bug` (red), `feedback:wrong-data` (orange), `feedback:idea` (blue), `feedback:other` (gray)
   - `paper:*` labels created lazily by the API route
4. (Later, if needed) Add `HCAPTCHA_SECRET` + `NEXT_PUBLIC_HCAPTCHA_SITE_KEY` to enable CAPTCHA

These are listed in the PR description and `deploy/README.md`, not done by the code at runtime.

## Observability (prod)

- Server logs one structured line per request: `{requestId, type, ok, status, durationMs, gh_status?}`. No IP, no email, no body content.
- Track failure rate via existing log shipping (if any) — flag if `502` rate exceeds 5% / hour.
- Optional weekly maintainer reminder: GH Action that comments on issues with `needs-triage` older than 7 days.

## Open questions resolved by reasonable-call

- **Auth:** anonymous, optional email contact
- **Storage:** GitHub issues only; no local DB row
- **Anti-spam:** honeypot + rate-limit + origin check + time-to-fill at launch; hCaptcha wired but off
- **PAT vs GitHub App:** fine-grained PAT for simplicity, 1-year expiry, rotation reminder. Upgrade to GitHub App if PAT rotation becomes painful
- **Repo target:** same repo (`OrFrederick/SepsisAtlas`) — env-driven so staging can point elsewhere
- **Tagging:** every issue gets `feedback`, `from-website`, `feedback:<type>`, `needs-triage`, plus `paper:<stem>` when applicable
