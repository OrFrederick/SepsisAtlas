---
type: "query"
date: "2026-05-30T16:52:01.197825+00:00"
question: "Are buildViewerUrl and viewerHrefFor intentional duplicates or a refactor target?"
contributor: "graphify"
source_nodes: ["buildViewerUrl()", "viewerHrefFor()", "Anchor contract", "ChatShell", "rank.ts", "rank.test.ts", "RankTable.tsx"]
---

# Q: Are buildViewerUrl and viewerHrefFor intentional duplicates or a refactor target?

## Answer

Expanded via vocab: [build, viewer, url, href, encode, jump, bbox, page, params, origin, chat, rank]. Connected by a single 1-hop semantically_similar_to INFERRED edge — no structural call/import path. buildViewerUrl lives in web/src/components/ChatShell.tsx (C29 Chat Shell, degree 4, only ChatShell calls it, no unit test). viewerHrefFor lives in web/src/lib/rank.ts (C11 Web Components Core, degree 6, imported by RankTable.tsx + tested by rank.test.ts, also references FastAPI endpoints /papers, /papers/{file}, /rows). Verdict: organic duplication, viewerHrefFor is the better-factored one. Refactor target: have ChatShell import viewerHrefFor from lib/rank.ts or hoist both into lib/viewerUrl.ts and delete buildViewerUrl.

## Source Nodes

- buildViewerUrl()
- viewerHrefFor()
- Anchor contract
- ChatShell
- rank.ts
- rank.test.ts
- RankTable.tsx