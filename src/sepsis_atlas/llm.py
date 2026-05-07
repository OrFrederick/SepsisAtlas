import json
import time
import uuid
import hashlib
import functools
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from sepsis_atlas.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    LOGS_DIR,
)

_LOG_PATH = LOGS_DIR / "llm_calls.jsonl"

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            timeout=300.0,
            max_retries=3,
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
                tokens_in = getattr(getattr(resp, "usage", None), "prompt_tokens", 0) if resp else 0
                tokens_out = getattr(getattr(resp, "usage", None), "completion_tokens", 0) if resp else 0
                cost_usd = float(
                    getattr(getattr(resp, "usage", None), "total_cost", 0.0) or 0.0
                ) if resp else 0.0
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
