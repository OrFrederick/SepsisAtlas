"""Live tests for kg_extractor against a local Neo4j. Skips cleanly if unreachable."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from api.backends.kg_store import KGStore
from extract.kg_extractor import extract_paper_structure


NEO4J_HOST = "localhost"
NEO4J_PORT = 7687


def _alive(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _alive(NEO4J_HOST, NEO4J_PORT),
    reason=f"neo4j not running on {NEO4J_HOST}:{NEO4J_PORT}",
)


SYNTHETIC_MD = """# Introduction
Some intro text.
<!-- page: 1 -->

## Methods
| Var | Mean |
|-----|------|
| Age | 64   |

![Figure 1](fig1.png)

## References
1. Smith J. et al. *Sepsis*. Lancet. 2020. https://doi.org/10.1016/S0140-6736(20)30000-0
2. Doe A. ICU outcomes. NEJM. 2019.
"""

PAPER_META = {
    "paper_ref": "Test 2020",
    "doi": "10.0000/test",
    "title": "Test Paper",
    "year": 2020,
    "source": "synthetic",
}


@pytest.fixture
def store():
    s = KGStore()
    s.clear_all()
    s.bootstrap_schema()
    try:
        yield s
    finally:
        s.clear_all()
        s.close()


@pytest.fixture
def synthetic_md_path(tmp_path: Path) -> Path:
    p = tmp_path / "test_paper.md"
    p.write_text(SYNTHETIC_MD, encoding="utf-8")
    return p


def test_extract_synthetic_counts(store, synthetic_md_path):
    summary = extract_paper_structure("test_paper", synthetic_md_path, PAPER_META, store)
    assert summary == {
        "file_stem": "test_paper",
        "n_sections": 3,
        "n_tables": 1,
        "n_figures": 1,
        "n_references": 2,
    }

    rows = store.run(
        "MATCH (p:Paper {file_name: $fn}) "
        "RETURN p.n_sections AS s, p.n_tables AS t, p.n_figures AS f, p.n_references AS r",
        fn="test_paper",
    )
    assert rows == [{"s": 3, "t": 1, "f": 1, "r": 2}]


def test_section_levels_and_doi(store, synthetic_md_path):
    extract_paper_structure("test_paper", synthetic_md_path, PAPER_META, store)
    methods = store.run(
        "MATCH (s:Section {paper_file_name: 'test_paper', heading: 'Methods'}) RETURN s.level AS lvl"
    )
    assert methods == [{"lvl": 2}]

    refs = store.run(
        "MATCH (r:Reference {paper_file_name: 'test_paper'}) "
        "RETURN r.ref_id AS id, r.doi AS doi ORDER BY r.ref_id"
    )
    assert refs[0]["id"] == "test_paper__ref_000"
    assert refs[0]["doi"] == "10.1016/S0140-6736(20)30000-0"
    assert refs[1]["doi"] is None


def test_edges_from_paper(store, synthetic_md_path):
    extract_paper_structure("test_paper", synthetic_md_path, PAPER_META, store)
    edges = store.run(
        """
        MATCH (p:Paper {file_name: 'test_paper'})-[r]->(n)
        RETURN type(r) AS rel, count(*) AS c
        """
    )
    by_rel = {e["rel"]: e["c"] for e in edges}
    assert by_rel == {
        "HAS_SECTION": 3,
        "HAS_TABLE": 1,
        "HAS_FIGURE": 1,
        "HAS_REFERENCE": 2,
    }


def test_table_csv_and_caption(store, synthetic_md_path):
    extract_paper_structure("test_paper", synthetic_md_path, PAPER_META, store)
    tbl = store.run(
        "MATCH (t:PaperTable {paper_file_name: 'test_paper'}) RETURN t.csv AS csv, t.page AS page"
    )
    assert len(tbl) == 1
    csv_text = tbl[0]["csv"]
    assert "Var,Mean" in csv_text
    assert "Age,64" in csv_text


def test_idempotency(store, synthetic_md_path):
    extract_paper_structure("test_paper", synthetic_md_path, PAPER_META, store)
    extract_paper_structure("test_paper", synthetic_md_path, PAPER_META, store)
    counts = store.run(
        """
        MATCH (n)
        RETURN labels(n)[0] AS label, count(*) AS c
        ORDER BY label
        """
    )
    by_label = {c["label"]: c["c"] for c in counts}
    assert by_label == {
        "Paper": 1,
        "Section": 3,
        "PaperTable": 1,
        "Figure": 1,
        "Reference": 2,
    }
    edges = store.run("MATCH ()-[r]->() RETURN count(r) AS c")
    assert edges == [{"c": 7}]


CAO_PARSED_JSON = Path("data/papers/parsed/Cao_2021.json")
CAO_PARSED_MD = Path("data/papers/parsed/Cao_2021.md")


@pytest.mark.skipif(
    not (CAO_PARSED_MD.exists() or CAO_PARSED_JSON.exists()),
    reason="Cao_2021 parsed file missing",
)
def test_extract_cao_2021_has_sections(store, tmp_path):
    if CAO_PARSED_MD.exists():
        md_path = CAO_PARSED_MD
    else:
        from extract.kg_extractor import _docling_json_to_markdown

        md_text = _docling_json_to_markdown(CAO_PARSED_JSON)
        md_path = tmp_path / "Cao_2021.md"
        md_path.write_text(md_text, encoding="utf-8")

    meta = {
        "paper_ref": "Cao 2021",
        "doi": "10.12659/MSM.932227",
        "title": None,
        "year": 2021,
        "source": "provided",
    }
    summary = extract_paper_structure("Cao_2021", md_path, meta, store)
    assert summary["n_sections"] > 0
