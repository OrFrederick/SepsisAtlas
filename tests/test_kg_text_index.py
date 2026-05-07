"""Live + offline tests for ``KGTextIndex``.

The live build / search tests hit OpenRouter and are skipped without an
``OPENROUTER_API_KEY`` (matches the pattern in ``tests/test_demo_live.py``).
The cache-reload test stubs the OpenAI SDK to assert that no embedding
call is made on a warm second build.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from api.backends.kg_text_index import KGTextIndex, _chunk_paper, _split_into_sections


REPO_ROOT = Path(__file__).resolve().parents[1]
PAPERS_DIR = REPO_ROOT / "data" / "papers" / "parsed"


def _papers_present() -> bool:
    if not PAPERS_DIR.exists():
        return False
    return any(PAPERS_DIR.glob("*.md")) or any(PAPERS_DIR.glob("*.json"))


live_only = pytest.mark.skipif(
    not os.getenv("OPENROUTER_API_KEY"),
    reason="OPENROUTER_API_KEY not set",
)
corpus_required = pytest.mark.skipif(
    not _papers_present(),
    reason="data/papers/parsed/ is empty",
)


def test_split_into_sections_uses_atx_headings():
    md = (
        "<!-- page: 1 -->\n"
        "# Intro\n"
        "alpha\n"
        "<!-- page: 2 -->\n"
        "## Methods\n"
        "beta gamma\n"
        "### Sub\n"
        "delta\n"
    )
    secs = _split_into_sections(md)
    headings = [s["heading"] for s in secs]
    assert headings == ["Intro", "Methods", "Sub"]
    pages = [s["page"] for s in secs]
    assert pages == [1, 2, 2]


def test_chunk_paper_skips_heading_only_sections():
    md = "# Empty\n\n# Real\nbody text here\n"
    chunks = _chunk_paper("paper", md)
    assert len(chunks) == 1
    assert chunks[0]["section_heading"] == "Real"
    assert chunks[0]["snippet"].startswith("body")


def test_chunk_paper_sliding_window_for_long_sections():
    long_body = ("sentence. " * 400).strip()
    md = f"# Big\n{long_body}\n"
    chunks = _chunk_paper("paper", md)
    assert len(chunks) >= 2
    assert all(c["section_heading"] == "Big" for c in chunks)
    assert all(c["chunk_id"].startswith("paper#") for c in chunks)


@live_only
@corpus_required
def test_build_and_search_live(tmp_path):
    cache = tmp_path / "embeddings.npz"
    idx = KGTextIndex(papers_dir=PAPERS_DIR, cache_path=cache)
    idx.build_or_load()
    assert idx.n_chunks > 0
    assert cache.exists()

    hits = idx.search("lactate predicts mortality", k=3)
    assert len(hits) == 3
    for h in hits:
        assert 0.0 <= h["score"] <= 1.0
        assert isinstance(h["snippet"], str) and h["snippet"]
        assert isinstance(h["paper_file_name"], str)
    assert any("lactate" in (h["snippet"] or "").lower() for h in hits)


@live_only
@corpus_required
def test_search_pinned_to_paper(tmp_path):
    cache = tmp_path / "embeddings.npz"
    idx = KGTextIndex(papers_dir=PAPERS_DIR, cache_path=cache)
    idx.build_or_load()

    target = "Cao_2021"
    has_target = any(m.get("paper_file_name") == target for m in idx._metadata)
    if not has_target:
        pytest.skip(f"{target} not in corpus")

    hits = idx.search("anything", paper_file_name=target, k=2)
    assert len(hits) == 2
    assert all(h["paper_file_name"] == target for h in hits)


@live_only
@corpus_required
def test_cache_reload_makes_no_api_calls(tmp_path):
    cache = tmp_path / "embeddings.npz"
    idx_a = KGTextIndex(papers_dir=PAPERS_DIR, cache_path=cache)
    idx_a.build_or_load()
    n_first = idx_a.n_chunks
    assert n_first > 0

    idx_b = KGTextIndex(papers_dir=PAPERS_DIR, cache_path=cache)
    with patch.object(KGTextIndex, "_embed") as mock_embed:
        idx_b.build_or_load()
        assert mock_embed.call_count == 0
    assert idx_b.n_chunks == n_first


def test_chunks_for_paper_does_not_call_api(tmp_path):
    if not _papers_present():
        pytest.skip("no corpus")
    sample = next(iter(PAPERS_DIR.glob("*.json")), None) or next(
        iter(PAPERS_DIR.glob("*.md")), None
    )
    assert sample is not None
    cache = tmp_path / "embeddings.npz"
    idx = KGTextIndex(papers_dir=PAPERS_DIR, cache_path=cache)
    chunks = idx.chunks_for_paper(sample.stem)
    assert len(chunks) > 0
    for c in chunks:
        assert c["paper_file_name"] == sample.stem
        assert "snippet" in c
        assert "chunk_id" in c
