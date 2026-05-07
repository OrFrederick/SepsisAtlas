# Docker stack — Sepsis Atlas demo

End-to-end local demo: FastAPI backend + Open WebUI + Pipelines + 3 OpenAPI
tool servers, all on one shared bridge network.

## Quick start

```bash
docker compose up --build
# wait for backend health check, then:
open http://localhost:3000
```

Services:

| Service        | Port | Role                                                  |
|----------------|------|-------------------------------------------------------|
| `backend`      | 8000 | FastAPI: `/query`, `/viewer`, `/forest_plot`, `/ingest_pubmed` |
| `openwebui`    | 3000 | Chat UI                                               |
| `pipelines`    | 9099 | Plugin runtime; mounts `./pipelines/sepsis_atlas.py`  |
| `open_source`  | 9101 | OpenAPI tool: PDF iframe                              |
| `meta_analyze` | 9102 | OpenAPI tool: forest plot panel                       |
| `expand_pubmed`| 9103 | OpenAPI tool: PubMed ingest progress                  |

## One-time setup inside Open WebUI

1. **Connect Pipelines.** `Admin → Settings → Connections → OpenAI API`
   - Base URL: `http://pipelines:9099`
   - API Key:  `0p3n-w3bu!` (matches `PIPELINES_API_KEY` in compose)
   - You will now see **"Sepsis Atlas"** in the model picker.

2. **Register the 3 tools.** `Admin → Settings → Tools → "+"`
   - `http://open_source:9101/openapi.json`
   - `http://meta_analyze:9102/openapi.json`
   - `http://expand_pubmed:9103/openapi.json`
   - Or import the merged spec at `tools/sepsis_atlas/openapi.yaml`.

3. **Enable iframe sandbox same-origin.**
   `Settings → Interface → "Allow Iframe Sandbox Same-Origin Access" → ON`
   Without this the PDF.js viewer will throw a cross-origin error inside the
   artifact pane.

4. **Pick the model.** New chat → model picker → **Sepsis Atlas**.

## Iframe security model

- Tool HTML responses set `X-OpenWebUI-Artifact: true` so v0.6+ Open WebUI
  routes them into the artifact pane. Older builds (no artifact router) still
  render them inline because the markdown HTML renderer accepts iframes when
  the same-origin sandbox toggle is on.
- The iframe inside each tool response is:
  ```html
  <iframe src="http://backend:8000/viewer/..."
          sandbox="allow-scripts allow-same-origin"
          referrerpolicy="no-referrer">
  ```
- Origins allowed for embedding the viewer come from
  `WEBUI_AUTH_TRUSTED_IFRAME_ORIGINS` on the openwebui container — currently
  `http://backend:8000,http://localhost:8000`.

## Outside-compose usage

If you run the backend locally (`uvicorn api.main:app --port 8000`) and only
the tool servers in compose, set `BACKEND_URL=http://host.docker.internal:8000`
on each tool service (Linux: add `extra_hosts: ["host.docker.internal:host-gateway"]`).

## Troubleshooting

- **"No models available"** → Pipelines container can't import
  `pipelines/sepsis_atlas.py`. Check `docker logs sepsis_pipelines`. Common
  cause: missing `httpx` in the base image (it's bundled in
  `ghcr.io/open-webui/pipelines:main`; if you pin an older tag, install it).
- **Forest plot 404** → backend's `/forest_plot/<query_id>.png` not yet
  generated. Issue a `/query` first; it returns `query_id` and writes the PNG.
- **PDF viewer blank** → same-origin sandbox toggle off, or
  `WEBUI_AUTH_TRUSTED_IFRAME_ORIGINS` doesn't include the backend host.
- **Tool calls return JSON instead of HTML** → some Open WebUI builds wrap
  tool responses; toggle "Render HTML tool responses as artifacts" in tool
  settings, or rely on the markdown image embedded by the pipeline.

## API surprises encountered while building this (May 2026)

- **Tools are OpenAPI servers now**, not the older `Tools` Python class.
  The class-based `Tools` API still works for in-process tools, but the
  preferred integration is "OpenAPI Tool Servers" registered by URL — that's
  what we ship.
- **`X-OpenWebUI-Artifact` header** is a community convention used by the
  artifact router; it isn't formally documented. Setting it is harmless on
  builds that ignore it.
- **Pipeline `pipe()`** signature is unchanged in 0.5.x / 0.6.x:
  `pipe(self, user_message, model_id, messages, body) -> str | Generator | Iterator`.
  Returning a generator streams tokens as they're yielded (one yielded chunk
  ≈ one server-sent event).
- **Cross-origin iframe error** inside the artifact pane requires the
  `Settings → Interface → Allow Iframe Sandbox Same-Origin Access` toggle on
  recent builds.
