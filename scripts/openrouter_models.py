#!/usr/bin/env python3
"""List OpenRouter models reachable with a given API key.

Usage:
    python scripts/openrouter_models.py [--key KEY] [--free] [--json] [--filter SUBSTR]

Key resolution order: --key arg, $OPENROUTER_API_KEY env var.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

BASE = "https://openrouter.ai/api/v1"


def _get(path: str, key: str) -> dict:
    req = Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {key}"})
    try:
        with urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        sys.exit(f"HTTP {e.code} on {path}: {body}")
    except URLError as e:
        sys.exit(f"network error on {path}: {e.reason}")


def fetch_key_info(key: str) -> dict:
    return _get("/auth/key", key).get("data", {})


def fetch_models(key: str) -> list[dict]:
    return _get("/models", key).get("data", [])


def is_free(m: dict) -> bool:
    p = m.get("pricing") or {}
    try:
        return float(p.get("prompt", 1)) == 0 and float(p.get("completion", 1)) == 0
    except (TypeError, ValueError):
        return False


def fmt_model(m: dict) -> str:
    p = m.get("pricing") or {}
    ctx = m.get("context_length") or m.get("top_provider", {}).get("context_length") or "?"
    prompt_p = p.get("prompt", "?")
    compl_p = p.get("completion", "?")
    return f"{m['id']:<55} ctx={ctx:<8} in={prompt_p:<12} out={compl_p}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", help="OpenRouter API key (else $OPENROUTER_API_KEY)")
    ap.add_argument("--free", action="store_true", help="Only zero-price models")
    ap.add_argument("--filter", help="Substring filter on model id")
    ap.add_argument("--json", action="store_true", help="Raw JSON output")
    args = ap.parse_args()

    key = args.key or os.getenv("OPENROUTER_API_KEY")
    if not key:
        sys.exit("missing API key: pass --key or set OPENROUTER_API_KEY")

    info = fetch_key_info(key)
    models = fetch_models(key)

    if args.filter:
        needle = args.filter.lower()
        models = [m for m in models if needle in m["id"].lower()]
    if args.free:
        models = [m for m in models if is_free(m)]

    models.sort(key=lambda m: m["id"])

    if args.json:
        print(json.dumps({"key": info, "models": models}, indent=2))
        return

    label = info.get("label", "?")
    limit = info.get("limit")
    usage = info.get("usage")
    free_tier = info.get("is_free_tier")
    print(f"key: label={label} usage={usage} limit={limit} free_tier={free_tier}")
    print(f"models: {len(models)}")
    print("-" * 100)
    for m in models:
        print(fmt_model(m))


if __name__ == "__main__":
    main()
