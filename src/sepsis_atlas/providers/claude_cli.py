"""Local-Claude (`claude` CLI) provider — drop-in for the OpenAI client.

Spawns `claude -p --output-format=json --model X --system-prompt Y "user msg"`
per request and maps the JSON envelope back to an OpenAI ChatCompletion-shape
so existing extractor callsites (`client.chat.completions.create(...)` →
`resp.choices[0].message.content`, `resp.usage.prompt_tokens`) keep working.

Why a subprocess shim and not the Anthropic Python SDK: the user explicitly
wants subscription auth (no `ANTHROPIC_API_KEY`). The CLI handles
OAuth/keychain login transparently; the SDK does not.

Caveats:
- Subprocess startup adds ~0.5s overhead per call vs. an in-process HTTP client.
- `temperature` / `max_tokens` aren't exposed by the CLI; we silently drop them.
  CC uses the model's default sampler, which is fine for our extraction prompts.
- `response_format={"type": "json_object"}` translates to a "respond with valid
  JSON only" suffix in the user message. If you need stricter schema
  enforcement, pass `--json-schema` via `extra_cli_args`.
- The CLI cwd matters: claude auto-loads CLAUDE.md from the working directory.
  We override the cwd to a tmp dir so model context stays predictable.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ---- model name translation ------------------------------------------------

# OpenRouter uses provider-prefixed names (`anthropic/claude-sonnet-4.6`).
# The CC CLI accepts either short aliases (`sonnet`, `opus`) or hyphenated
# full names (`claude-sonnet-4-6`). Map known OR names to CC names.
_OR_TO_CC_MODEL = {
    "anthropic/claude-opus-4.7": "claude-opus-4-7",
    "anthropic/claude-opus-4.6": "claude-opus-4-6",
    "anthropic/claude-sonnet-4.6": "claude-sonnet-4-6",
    "anthropic/claude-sonnet-4.5": "claude-sonnet-4-5",
    "anthropic/claude-haiku-4.5": "claude-haiku-4-5",
    "anthropic/claude-haiku-4.6": "claude-haiku-4-6",
}


def _translate_model(model: str) -> str:
    if not model:
        return "sonnet"
    if model in _OR_TO_CC_MODEL:
        return _OR_TO_CC_MODEL[model]
    # Already a CC-shaped name (sonnet / claude-opus-4-7 / etc.) — pass through.
    if model.startswith("anthropic/"):
        # Best-effort: strip prefix and replace dots with dashes.
        return model.removeprefix("anthropic/").replace(".", "-")
    return model


# ---- response shape (OpenAI-compatible) -----------------------------------


@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0


@dataclass
class _Message:
    role: str = "assistant"
    content: str = ""


@dataclass
class _Choice:
    index: int = 0
    finish_reason: str = "stop"
    message: _Message = field(default_factory=_Message)


@dataclass
class _ChatCompletion:
    id: str
    model: str
    choices: list[_Choice]
    usage: _Usage


# ---- client ---------------------------------------------------------------


class _Completions:
    def __init__(self, parent: "ClaudeCLIClient") -> None:
        self._parent = parent

    def create(self, *, messages: list[dict], model: str, **kwargs: Any) -> _ChatCompletion:
        return self._parent._call(messages=messages, model=model, **kwargs)


class _Chat:
    def __init__(self, parent: "ClaudeCLIClient") -> None:
        self.completions = _Completions(parent)


class ClaudeCLIClient:
    """Subprocess-backed adapter exposing the slice of the OpenAI client
    surface our extractors actually use: `.chat.completions.create()`."""

    def __init__(
        self,
        bin_path: str = "claude",
        timeout_s: int = 600,
        extra_cli_args: list[str] | None = None,
    ) -> None:
        self._bin = bin_path
        self._timeout = timeout_s
        self._extra = list(extra_cli_args or [])
        # One persistent tmp dir per client so CC's prompt cache survives
        # across calls. Without this every call cold-starts the cache, which
        # blows up latency on long extraction prompts.
        self._cwd = tempfile.mkdtemp(prefix="claude-cli-")
        self.chat = _Chat(self)

    def _call(
        self,
        *,
        messages: list[dict],
        model: str,
        response_format: dict | None = None,
        **_: Any,  # silently ignore temperature / max_tokens / etc.
    ) -> _ChatCompletion:
        system_parts: list[str] = []
        user_parts: list[str] = []
        for m in messages:
            role = m.get("role")
            content = m.get("content") or ""
            if not isinstance(content, str):
                # Multimodal arrays — claude CLI doesn't accept them; flatten.
                content = json.dumps(content)
            if role == "system":
                system_parts.append(content)
            elif role in ("user", "assistant"):
                # Multi-turn dialogue: serialize as labeled blocks. The
                # extractor only uses single-turn so this rarely fires.
                prefix = "USER" if role == "user" else "ASSISTANT"
                user_parts.append(f"<{prefix}>\n{content}\n</{prefix}>")
        user_text = "\n\n".join(user_parts) or ""

        # response_format=json_object: nudge the model in-prompt. The CLI
        # does support --json-schema for strict validation but our
        # extraction prompts already enforce JSON shape via system prompt.
        if isinstance(response_format, dict) and response_format.get("type") == "json_object":
            user_text = (
                user_text
                + "\n\nRespond with a single valid JSON object. No prose, no code fences."
            )

        argv = [
            self._bin,
            "-p",
            "--no-session-persistence",
            "--output-format", "json",
            "--model", _translate_model(model),
        ]
        if system_parts:
            argv += ["--system-prompt", "\n\n".join(system_parts)]
        argv += self._extra
        argv.append(user_text)

        # Run from the persistent tmp dir so CLAUDE.md / hooks from any
        # random cwd don't pollute the context, and CC's prompt cache
        # survives across calls (the cache key includes cwd).
        t0 = time.time()
        try:
            proc = subprocess.run(
                argv,
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"claude CLI timed out after {self._timeout}s"
            ) from e
        duration_s = time.time() - t0

        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI exited {proc.returncode} after {duration_s:.1f}s; "
                f"stderr head: {proc.stderr[:300]!r}"
            )

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"claude CLI emitted unparseable stdout (first 300 chars): "
                f"{proc.stdout[:300]!r}"
            ) from e

        if envelope.get("is_error"):
            raise RuntimeError(
                f"claude CLI reported error: {envelope.get('result', '<no result>')}"
            )

        usage_raw = envelope.get("usage") or {}
        usage = _Usage(
            prompt_tokens=int(usage_raw.get("input_tokens", 0)),
            completion_tokens=int(usage_raw.get("output_tokens", 0)),
            total_tokens=int(usage_raw.get("input_tokens", 0))
            + int(usage_raw.get("output_tokens", 0)),
            cost=float(envelope.get("total_cost_usd", 0.0) or 0.0),
        )
        return _ChatCompletion(
            id=str(envelope.get("session_id") or uuid.uuid4()),
            model=envelope.get("model")
            or next(iter(envelope.get("modelUsage", {}) or {}), "")
            or _translate_model(model),
            choices=[
                _Choice(
                    message=_Message(role="assistant", content=str(envelope.get("result") or "")),
                ),
            ],
            usage=usage,
        )
