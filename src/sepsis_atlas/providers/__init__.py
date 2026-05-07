"""LLM provider adapters.

Two providers ship today:

  * **openrouter** (default) — `openai.OpenAI(base_url=OPENROUTER_BASE_URL)`.
    Standard chat-completions API; requires `OPENROUTER_API_KEY`.
  * **claude-cli** — shells out to the local `claude` binary (Claude Code).
    No API key; uses the user's OAuth/subscription auth. Useful when
    OpenRouter is down or credits are exhausted.

Both expose a thin `.chat.completions.create()` shim so existing extractor
callsites keep working without changes. The provider switch is the
`LLM_PROVIDER` env var read in `sepsis_atlas.config`.
"""

from .claude_cli import ClaudeCLIClient

__all__ = ["ClaudeCLIClient"]
