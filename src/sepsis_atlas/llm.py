import json
import time
import uuid
import hashlib
import functools
from pathlib import Path
from typing import Any, Callable

from sepsis_atlas.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    LOGS_DIR,
    LANGFUSE_PUBLIC_KEY,
    LANGFUSE_SECRET_KEY,
    LANGFUSE_HOST,
    LLM_PROVIDER,
    CLAUDE_CLI_BIN,
    CLAUDE_CLI_TIMEOUT_S,
)

# Conditional Langfuse-traced OpenAI client. Falls back silently when keys absent
# or when langfuse import / network fails. The bug that broke this previously
# was upstream Cloudflare 524 returning choices=None which Langfuse couldn't
# handle; that's been fixed at the extractor level by switching off strict
# json_schema, so the wrapper is safe to re-enable here.
if LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY:
    import os as _os
    _os.environ.setdefault("LANGFUSE_PUBLIC_KEY", LANGFUSE_PUBLIC_KEY)
    _os.environ.setdefault("LANGFUSE_SECRET_KEY", LANGFUSE_SECRET_KEY)
    _os.environ.setdefault("LANGFUSE_HOST", LANGFUSE_HOST)
    try:
        from langfuse.openai import OpenAI  # type: ignore
        _LANGFUSE_ENABLED = True
    except Exception:
        from openai import OpenAI  # type: ignore
        _LANGFUSE_ENABLED = False
else:
    from openai import OpenAI  # type: ignore
    _LANGFUSE_ENABLED = False

_LOG_PATH = LOGS_DIR / "llm_calls.jsonl"

# Typed as `Any` because `claude-cli` returns our adapter, not an OpenAI client.
# Both expose `.chat.completions.create(messages=..., model=..., **kwargs)`,
# which is the only surface our extractors touch.
_client: Any | None = None


def get_client() -> Any:
    """Return whatever provider `LLM_PROVIDER` selects.

    Default `openrouter` returns an OpenAI-shaped client pointing at
    OpenRouter. `claude-cli` returns the local-CLI adapter; same
    `.chat.completions.create()` surface, no API key required.
    """
    global _client
    if _client is not None:
        return _client

    provider = (LLM_PROVIDER or "openrouter").strip().lower()
    if provider == "claude-cli":
        from sepsis_atlas.providers.claude_cli import ClaudeCLIClient

        _client = ClaudeCLIClient(bin_path=CLAUDE_CLI_BIN, timeout_s=CLAUDE_CLI_TIMEOUT_S)
        return _client

    # Default: OpenRouter via OpenAI client.
    # max_retries=1: each retry re-uploads the full paper context (50-100k
    # tokens for predictor_extract). 3 retries on transient errors = 4×
    # input cost on a failed call. Cap at 1; higher-level retry policies
    # belong in the extractor with proper dedup.
    _client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout=300.0,
        max_retries=1,
        default_headers={
            "HTTP-Referer": "https://github.com/sepsis-atlas",
            "X-Title": "Sepsis Atlas",
        },
    )
    return _client


def _hash_prompt(prompt: str | list[dict]) -> str:
    payload = prompt if isinstance(prompt, str) else json.dumps(prompt, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _append_log(record: dict) -> None:
    with _LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")


def logged_llm_call(stage: str):
    """Decorator that wraps an OpenRouter call and emits an audit row.

    The wrapped function must accept (messages, model, **kwargs) and return
    an OpenAI ChatCompletion-shaped object.
    """

    def deco(func: Callable[..., Any]):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            call_id = str(uuid.uuid4())
            run_id = kwargs.pop("run_id", None)
            row_id = kwargs.pop("row_id", None)
            paper_id = kwargs.pop("paper_id", None)
            query_id = kwargs.pop("query_id", None)
            prompt_id = kwargs.pop("prompt_id", "")

            messages = kwargs.get("messages") or (args[0] if args else None)
            model = kwargs.get("model") or (args[1] if len(args) > 1 else "")

            prompt_hash = _hash_prompt(messages or "")

            if _LANGFUSE_ENABLED:
                kwargs.setdefault("name", stage)
                kwargs.setdefault(
                    "metadata",
                    {
                        "stage": stage,
                        "run_id": run_id,
                        "row_id": row_id,
                        "paper_id": paper_id,
                        "query_id": query_id,
                        "prompt_id": prompt_id,
                        "prompt_hash": prompt_hash,
                    },
                )
            t0 = time.time()
            err = None
            resp = None
            try:
                resp = func(*args, **kwargs)
                return resp
            except Exception as e:
                err = repr(e)
                raise
            finally:
                latency_ms = int((time.time() - t0) * 1000)
                u = getattr(resp, "usage", None) if resp else None
                tokens_in = getattr(u, "prompt_tokens", 0) if u else 0
                tokens_out = getattr(u, "completion_tokens", 0) if u else 0
                cost_usd = float(getattr(u, "total_cost", 0.0) or 0.0) if u else 0.0
                cache_creation = int(getattr(u, "cache_creation_input_tokens", 0) or 0) if u else 0
                cache_read = int(getattr(u, "cache_read_input_tokens", 0) or 0) if u else 0
                record = {
                    "call_id": call_id,
                    "ts": time.time(),
                    "stage": stage,
                    "run_id": run_id,
                    "row_id": row_id,
                    "paper_id": paper_id,
                    "query_id": query_id,
                    "model": model,
                    "prompt_id": prompt_id,
                    "prompt_hash": prompt_hash,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "cache_creation_tokens": cache_creation,
                    "cache_read_tokens": cache_read,
                    "cost_usd": cost_usd,
                    "latency_ms": latency_ms,
                    "error": err,
                }
                _append_log(record)

        return wrapper

    return deco


@logged_llm_call(stage="generic")
def chat(messages: list[dict], model: str, **kwargs):
    """Default OpenRouter chat-completion call. Override per-stage as needed."""
    client = get_client()
    return client.chat.completions.create(messages=messages, model=model, **kwargs)
