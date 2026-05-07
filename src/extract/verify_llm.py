"""LLM-as-judge verifier (tier 2 of the tiered verifier).

The local NLI verifier returns *neutral* on table-cell anchors because the
cell text isn't a clausal premise; that drops the score and tags many correct
extractions as `partial`. This module replaces NLI with a Haiku-4.5 judge
that sees:

    1. the structured claim (predictor / outcome / numeric fields),
    2. the verbatim ``source_span`` (anchor text),
    3. the **entire parsed paper** as grounding context (capped at 150k
       chars — well within Haiku 4.5's 200k window).

The judge returns JSON ``{verdict, score, rationale, supported_atoms,
contradicted_atoms}`` which we map directly to a ``VerifierResponse``.

Cost control
------------
We send the full paper as a separate SYSTEM message block with
``cache_control={"type":"ephemeral"}`` so Anthropic prompt-caching kicks in
on the second-and-following rows for the same paper. Callers
(``reverify.py``) order rows by ``paper_id`` to maximise the cache hit rate.

We also keep a client-side SQLite cache keyed on
``sha256(canonical_claim, source_span, paper_text, model)``. This makes
reverifies idempotent regardless of provider caching.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from sepsis_atlas import config as _saconfig
from sepsis_atlas.config import DB_PATH, PAPERS_PARSED
from sepsis_atlas.llm import get_client, logged_llm_call
from sepsis_atlas.schemas import VerifierResponse


# Resolve MODEL_VERIFY_LLM from the shared config if it's been added there,
# otherwise fall back to the env var directly. This keeps the worktree
# working even when the installed sepsis_atlas package predates the new
# constant — the brief asked us to add it to ``sepsis_atlas.config`` but the
# worktree may run against an older editable install.
MODEL_VERIFY_LLM = getattr(
    _saconfig,
    "MODEL_VERIFY_LLM",
    os.getenv("MODEL_VERIFY_LLM", "anthropic/claude-haiku-4.5"),
)


# ---------------------------------------------------------------------------
# Prompt — three-shot, anonymized examples (no Gai/Seymour/Wang/Zhang content)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a strict scientific-evidence verifier.

You are given:
  * a structured CLAIM (a predictor/outcome row with numeric effect sizes),
  * a verbatim SOURCE_SPAN (the anchor text the extractor cited),
  * the FULL PARSED PAPER as grounding context (text + tables).

Decide whether the SOURCE_SPAN, read in the context of the full paper,
supports the CLAIM. The paper is for grounding — numbers must still come
from the SOURCE_SPAN (or the table row that contains the SOURCE_SPAN).

Return ONLY a JSON object with these keys:
{
  "verdict": "ok" | "partial" | "reject",
  "score":   number in [0,1],
  "rationale": short string (<=300 chars),
  "supported_atoms":   list of atom names that the source supports,
  "contradicted_atoms": list of atom names that the source contradicts
}

Atoms to consider (only those present in the claim count):
  * predictor / outcome  — does the span/section concern this predictor and outcome?
  * effect_value, ci_lo, ci_hi, p_value — do these numbers appear in the span/table row?
  * auc, sens, spec, ppv, npv, c_index — same.
  * cohort / population — does the surrounding section describe the cohort the
    claim attaches to (if the claim carries population_description /
    population_location / outcome_window_days)?

Verdict policy:
  * "ok"      — every atom in the claim is supported (or trivially absent in
                a way that doesn't change the meaning of the row).
  * "partial" — claim is broadly correct, but at least one atom is unverifiable
                from this span+context (e.g. a CI is given but a p-value is
                claimed without source).
  * "reject"  — at least one atom is *contradicted* by the source (different
                number, different cohort, different outcome window).

Output JSON only — no prose, no markdown fence.

----- WORKED EXAMPLE 1 (ok) -----
CLAIM: {"predictors":"serum lactate","outcome":"30-day mortality",
"effect_type":"OR","effect_value":1.45,"ci_lo":1.10,"ci_hi":1.92,"p_value":0.008}
SOURCE_SPAN: "Serum lactate 1.45 (1.10-1.92) 0.008"
(paper context contains a Table 3 with header "Multivariable logistic
regression for 30-day mortality" and the row "Serum lactate | 1.45
(1.10-1.92) | 0.008")
RESPONSE:
{"verdict":"ok","score":0.95,"rationale":"OR 1.45, CI 1.10-1.92 and p=0.008 all match the lactate row of the 30-day mortality regression table.","supported_atoms":["predictor","outcome","effect_value","ci_lo","ci_hi","p_value"],"contradicted_atoms":[]}

----- WORKED EXAMPLE 2 (reject — wrong outcome) -----
CLAIM: {"predictors":"procalcitonin","outcome":"28-day mortality",
"outcome_window_days":28,"effect_type":"HR","effect_value":2.10,"ci_lo":1.40,"ci_hi":3.15}
SOURCE_SPAN: "PCT >2.0 ng/mL was associated with ICU-discharge mortality (HR 2.10, 95% CI 1.40-3.15)."
(paper Methods state: "Outcome was death before ICU discharge; we did not
follow patients beyond ICU stay.")
RESPONSE:
{"verdict":"reject","score":0.15,"rationale":"Numbers match but outcome is ICU-discharge mortality, not 28-day mortality.","supported_atoms":["predictor","effect_value","ci_lo","ci_hi"],"contradicted_atoms":["outcome","outcome_window_days"]}

----- WORKED EXAMPLE 3 (partial — number unverifiable) -----
CLAIM: {"predictors":"NEWS score","outcome":"in-hospital mortality",
"effect_type":"AUC","auc":0.81,"auc_ci_lo":0.77,"auc_ci_hi":0.85}
SOURCE_SPAN: "NEWS achieved an AUC of 0.81 for in-hospital mortality."
(paper says: "Diagnostic accuracy. NEWS achieved an AUC of 0.81 for
in-hospital mortality. Confidence intervals are reported in Supplementary
Table S2." and the supplementary table is NOT included in the parsed paper.)
RESPONSE:
{"verdict":"partial","score":0.7,"rationale":"AUC and outcome match; CI bounds are referenced but not present in the parsed paper.","supported_atoms":["predictor","outcome","auc"],"contradicted_atoms":[]}
"""


# ---------------------------------------------------------------------------
# LLM call (logged + structured output)
# ---------------------------------------------------------------------------


@logged_llm_call(stage="verifier_llm")
def _call_verify_llm(messages, model, **kwargs):
    return get_client().chat.completions.create(
        messages=messages, model=model, **kwargs
    )


# ---------------------------------------------------------------------------
# Cache (SQLite)
# ---------------------------------------------------------------------------


def _ensure_cache_table(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS verifier_llm_cache ("
        "input_hash TEXT PRIMARY KEY, "
        "response_json TEXT NOT NULL, "
        "ts REAL NOT NULL)"
    )


def _open_cache(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = str(db_path) if db_path else str(DB_PATH)
    con = sqlite3.connect(path, timeout=30.0)
    # WAL + a generous busy timeout: when reverify holds the main DB
    # connection mid-batch, the cache writer needs to coexist instead of
    # racing for the lock.
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=30000")
    except sqlite3.OperationalError:
        pass
    _ensure_cache_table(con)
    return con


def _cache_get(con: sqlite3.Connection, h: str) -> dict | None:
    row = con.execute(
        "SELECT response_json FROM verifier_llm_cache WHERE input_hash=?", (h,)
    ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None


def _cache_put(con: sqlite3.Connection, h: str, payload: dict) -> None:
    con.execute(
        "INSERT OR REPLACE INTO verifier_llm_cache(input_hash, response_json, ts) "
        "VALUES (?, ?, ?)",
        (h, json.dumps(payload), time.time()),
    )
    con.commit()


# ---------------------------------------------------------------------------
# Hash key — deterministic across re-runs
# ---------------------------------------------------------------------------


_HASH_FIELDS = (
    # text identifiers
    "predictors",
    "predictor_canonical",
    "outcome",
    "outcome_type",
    "outcome_window_days",
    "model_specification",
    "effect_size_str",
    "effect_type",
    # numerics
    "effect_value",
    "ci_lo",
    "ci_hi",
    "p_value",
    "auc",
    "auc_ci_lo",
    "auc_ci_hi",
    "sens",
    "spec",
    "ppv",
    "npv",
    "c_index",
    "cutoff",
    "cohort_size_n",
    "mortality_rate_pct",
    # cohort identifiers (relevant for the cross-check)
    "cohort_id",
    "population_description",
    "population_location",
)


def _canonical_claim(claim: dict) -> dict:
    """Pick a stable subset of claim fields for the cache key.

    We deliberately drop fields that aren't checkable (ts, run_id, anchor.bbox,
    field_status, ...) so re-runs hit cache even if surrounding metadata
    drifted between runs.
    """
    out: dict = {}
    for f in _HASH_FIELDS:
        v = claim.get(f)
        if v is None:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        out[f] = v
    return out


def _input_hash(
    claim: dict, source_span: str, paper_text: str, model: str
) -> str:
    """Hash claim + span + paper + model.

    The full paper is part of the key so that re-parsing a paper (which
    changes the text) invalidates the cache. SHA-256 over a stable JSON
    serialization keeps it deterministic across processes.
    """
    payload = json.dumps(
        {
            "claim": _canonical_claim(claim),
            "source_span": source_span or "",
            "paper_text": paper_text or "",
            "model": model,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Full-paper context loader (with table rendering + LRU per paper)
# ---------------------------------------------------------------------------


_MAX_PAPER_CHARS = 150_000  # well under Haiku 4.5's 200k context window


def _table_to_text(table: dict) -> str:
    """Render a Docling table as readable plain text, row-by-row."""
    cells = table.get("cells") or []
    if not cells:
        return ""
    rows: dict[int, list[tuple[int, str]]] = {}
    for c in cells:
        rows.setdefault(int(c.get("row", 0)), []).append(
            (int(c.get("col", 0)), str(c.get("text", "")))
        )
    lines: list[str] = []
    for r in sorted(rows.keys()):
        ordered = [t for _, t in sorted(rows[r], key=lambda x: x[0])]
        lines.append(" | ".join(ordered))
    return "\n".join(lines)


# Per-process LRU. The paper texts are large (10-100k chars), so we cap at 32
# entries — ~5MB max — to avoid blowing the worker's heap during a backfill.
_paper_text_cache: dict[str, str] = {}
_PAPER_CACHE_LIMIT = 32


def _load_paper_text(paper_id: str) -> str:
    """Return the full parsed paper as text (body + tables), capped at 150k chars.

    The Docling JSON has a ``full_text`` field for body text, plus
    ``tables[]`` with cell-level structure. We render each table as a
    pipe-separated block at the end so the judge can see numeric tables that
    aren't part of ``full_text``.
    """
    if not paper_id:
        return ""
    cached = _paper_text_cache.get(paper_id)
    if cached is not None:
        return cached

    path = PAPERS_PARSED / f"{paper_id}.json"
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return ""

    parts: list[str] = []
    title = data.get("title")
    if title:
        parts.append(f"TITLE: {title}")
    body = data.get("full_text") or ""
    if body:
        parts.append(body)

    for i, tbl in enumerate(data.get("tables") or []):
        rendered = _table_to_text(tbl)
        if not rendered:
            continue
        page = tbl.get("page", "?")
        parts.append(f"\n--- TABLE {i+1} (page {page}) ---\n{rendered}")

    full = "\n\n".join(parts)
    if len(full) > _MAX_PAPER_CHARS:
        full = full[:_MAX_PAPER_CHARS]

    if len(_paper_text_cache) >= _PAPER_CACHE_LIMIT:
        # Evict oldest insertion (Python dicts preserve insertion order).
        first_key = next(iter(_paper_text_cache))
        _paper_text_cache.pop(first_key, None)
    _paper_text_cache[paper_id] = full
    return full


# Backward-compat alias: older code (and tests) imported _load_parent_section.
def _load_parent_section(
    paper_id: str, anchor_text: str = "", anchor_section: str | None = None
) -> str:
    """Compatibility shim — returns the full paper text. The parameter name
    is kept for callers that haven't migrated yet."""
    return _load_paper_text(paper_id)


# ---------------------------------------------------------------------------
# JSON-response parsing
# ---------------------------------------------------------------------------


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _strip_fences(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    return s


def _parse_judge_response(raw: str) -> dict:
    """Parse the JSON envelope returned by the LLM.

    Raises ``ValueError`` if it is unparseable or missing required keys.
    """
    body = _strip_fences(raw)
    try:
        obj = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"verifier_llm: non-JSON response ({e}): {raw[:200]!r}") from e
    if not isinstance(obj, dict):
        raise ValueError(f"verifier_llm: expected JSON object, got {type(obj)}")
    verdict = obj.get("verdict")
    if verdict not in {"ok", "partial", "reject"}:
        raise ValueError(f"verifier_llm: invalid verdict {verdict!r}")
    try:
        score = float(obj.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    obj["score"] = score
    obj.setdefault("rationale", "")
    obj.setdefault("supported_atoms", [])
    obj.setdefault("contradicted_atoms", [])
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _build_user_message(claim: dict, source_span: str) -> str:
    return json.dumps(
        {
            "claim": _canonical_claim(claim),
            "source_span": source_span or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _build_system_messages(paper_text: str) -> list[dict]:
    """Return the structured system content blocks.

    Block 0 = stable instructions + worked examples (cacheable, but small,
    so we don't bother marking it ephemeral).

    Block 1 = the full parsed paper, marked ``cache_control:ephemeral`` so
    Anthropic prompt-caching reuses it across consecutive rows of the same
    paper. OpenRouter forwards the cache_control field through to Anthropic.
    """
    blocks: list[dict] = [{"type": "text", "text": _SYSTEM_PROMPT}]
    if paper_text:
        blocks.append(
            {
                "type": "text",
                "text": f"PAPER (full parsed text + tables):\n{paper_text}",
                "cache_control": {"type": "ephemeral"},
            }
        )
    return blocks


def run_llm_judge(
    claim: dict,
    source_span: str,
    paper_text: str,
    *,
    paper_id: str = "",
    row_id: str = "",
    run_id: str = "",
    model: str | None = None,
    cache_db_path: str | Path | None = None,
    cache_con: sqlite3.Connection | None = None,
) -> tuple[VerifierResponse, dict]:
    """LLM-as-judge verifier with full-paper context + deterministic cache.

    Parameters
    ----------
    claim : dict
        Structured claim (output of ``PredictorModelOut.model_dump()`` or a
        DB row rendered as a dict). Only the fields in ``_HASH_FIELDS``
        affect the cache key.
    source_span : str
        Verbatim ``anchor_text`` the extractor cited.
    paper_text : str
        The full parsed paper as text (≤150k chars). Sent as a separate
        SYSTEM block with Anthropic prompt-caching enabled. Pass the empty
        string if no paper is available — the judge can still rule on the
        claim ↔ span match without grounding.
    model : str
        OpenRouter model id; defaults to ``MODEL_VERIFY_LLM``.

    Returns
    -------
    (VerifierResponse, meta)
        ``meta`` mirrors the verifier meta dict produced by ``verify_nli``,
        with extra ``cache_hit`` and ``input_hash`` keys.
    """
    model = model or MODEL_VERIFY_LLM
    h = _input_hash(claim, source_span, paper_text, model)

    # If a caller (e.g. reverify) passed in their existing connection, reuse
    # it so we don't fight for the SQLite write lock. Otherwise open one.
    own_con = cache_con is None
    con = cache_con if cache_con is not None else _open_cache(cache_db_path)
    if own_con is False:
        _ensure_cache_table(con)
    try:
        cached = _cache_get(con, h)
        if cached is not None:
            resp = VerifierResponse(
                verdict=cached["verdict"],
                score=float(cached.get("score", 0.0)),
                rationale=str(cached.get("rationale", ""))[:400],
            )
            meta = {
                "model": model,
                "prompt_id": "verify_llm@v2",
                "latency_ms": 0,
                "tokens_in": 0,
                "tokens_out": 0,
                "cost_usd": 0.0,
                "cache_hit": True,
                "input_hash": h,
                "atoms_matched": list(cached.get("supported_atoms") or []),
                "atoms_contradict": list(cached.get("contradicted_atoms") or []),
                "atoms_absent": [],
            }
            return resp, meta

        # Cache miss — call the LLM with full-paper context + prompt caching.
        messages = [
            {"role": "system", "content": _build_system_messages(paper_text)},
            {"role": "user", "content": _build_user_message(claim, source_span)},
        ]
        t0 = time.time()
        resp_obj = _call_verify_llm(
            messages,
            model,
            response_format={"type": "json_object"},
            temperature=0,
            run_id=run_id,
            paper_id=paper_id,
            row_id=row_id,
            prompt_id="verify_llm@v2",
            extra_body={"anthropic_beta": "prompt-caching-2024-07-31"},
        )
        latency_ms = int((time.time() - t0) * 1000)

        if not getattr(resp_obj, "choices", None):
            err = getattr(resp_obj, "error", None)
            raise RuntimeError(f"verifier_llm: provider returned no choices ({err!r})")
        content = getattr(resp_obj.choices[0].message, "content", None)
        if not content:
            raise RuntimeError("verifier_llm: empty content")

        parsed = _parse_judge_response(content)
        _cache_put(con, h, parsed)

        usage = getattr(resp_obj, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) or 0
        tokens_out = getattr(usage, "completion_tokens", 0) or 0
        cost_reported = float(getattr(usage, "total_cost", 0.0) or 0.0)
        # OpenRouter's openai-compat response often omits total_cost; estimate
        # from Haiku 4.5 list pricing as a fallback so the running total is
        # informative. Haiku 4.5 list: $1/Mtok in, $5/Mtok out; cache reads
        # are ~10% of input. We don't see the cache-hit ratio from the usage
        # object so this slightly over-estimates when prompt caching helps.
        if cost_reported > 0.0:
            cost_usd = cost_reported
        else:
            cost_usd = tokens_in * 1e-6 + tokens_out * 5e-6
        meta = {
            "model": model,
            "prompt_id": "verify_llm@v2",
            "latency_ms": latency_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "cost_reported_usd": cost_reported,
            "cache_hit": False,
            "input_hash": h,
            "atoms_matched": list(parsed.get("supported_atoms") or []),
            "atoms_contradict": list(parsed.get("contradicted_atoms") or []),
            "atoms_absent": [],
        }
        verdict = VerifierResponse(
            verdict=parsed["verdict"],
            score=float(parsed["score"]),
            rationale=str(parsed.get("rationale", ""))[:400],
        )
        return verdict, meta
    finally:
        if own_con:
            con.close()
