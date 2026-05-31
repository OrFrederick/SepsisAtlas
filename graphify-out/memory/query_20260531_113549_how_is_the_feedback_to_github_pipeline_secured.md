---
type: "query"
date: "2026-05-31T11:35:49.489225+00:00"
question: "How is the feedback-to-GitHub pipeline secured?"
contributor: "graphify"
source_nodes: ["HMAC mount-token spam guard", "signMount()", "verifyMount()", "Sliding-window rate limiter", "RateLimiter", "Honeypot field anti-spam", "Markdown injection defense via dynamic-length code fences", "createFeedbackIssue()", "bodyFor()", "labelsFor()"]
---

# Q: How is the feedback-to-GitHub pipeline secured?

## Answer

Expanded via vocab: [feedback, hmac, mount, token, sliding, rate, limit, markdown, injection, honeypot, github, label]. 4-layer defense: (1) HMAC mount-token spam guard implemented by signMount/verifyMount in web/src/lib/feedback-mount.ts. GET /api/feedback/mount returns HMAC-signed (timestamp, signature) with server secret. POST must include token; verifyMount rejects expired/forged/replayed. Beats static CSRF: scraper gets a token but can't reuse after window, can't forge new ones without secret. Blocks headless bots that POST without GETting /mount. (2) Sliding-window rate limiter — RateLimiter in web/src/lib/rate-limit.ts with .check/.constructor/.size/.sweepExpired methods, route.ts imports it, .sweepExpired runs at check time (sliding not fixed). Keyed by clientIp from web/src/lib/rate-limit.ts. RUNS FIRST in POST handler — cheaper to short-circuit on rate than HMAC. (3) Honeypot field anti-spam — hidden form field bots fill but humans don't, cheap string-equality check. (4) Markdown injection defense via dynamic-length code fences in web/src/lib/github.ts (bodyFor). Scan user input for longest backtick run N, wrap in N+1 backticks. Tested github.test.ts. Full flow: FeedbackForm onmount GET signMount, onsubmit POST -> RateLimiter.check -> verifyMount -> honeypot check -> Zod validate -> createFeedbackIssue -> bodyFor (fence) + labelsFor (feedback:*/paper:*/needs-triage) + ensurePaperLabel (create paper:Foo_2024 if absent) + GitHub REST API with server-only fine-grained PAT (issue+label scope only, never in client bundle). Why over-engineered: issue tracker shared with paid maintainers, spam is operationally expensive; each layer catches different bot class — honeypot trivial, rate-limit volume, HMAC replay/no-mount, schema malformed payloads, markdown defense protects against human content attacks not bots.

## Source Nodes

- HMAC mount-token spam guard
- signMount()
- verifyMount()
- Sliding-window rate limiter
- RateLimiter
- Honeypot field anti-spam
- Markdown injection defense via dynamic-length code fences
- createFeedbackIssue()
- bodyFor()
- labelsFor()
- ensurePaperLabel()
- Server-only fine-grained PAT
- GitHub REST issues + labels API
- Feedback submission pipeline (form -> validate -> mount-verify -> github issue)