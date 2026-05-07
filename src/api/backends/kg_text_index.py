"""Embedding-backed full-text retrieval over parsed paper markdown.

Used by the agent loop's ``search_text`` tool to surface the most relevant
chunks of paper prose for a natural-language query, ranked by cosine
similarity over OpenAI ``text-embedding-3-large`` embeddings served via
OpenRouter.

The on-disk corpus under ``data/papers/parsed`` is Docling JSON, but the
class also accepts pre-rendered ``.md`` files in the same directory; JSON
input is rendered to markdown on the fly so the chunker only ever sees a
single canonical representation.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path

import numpy as np
from openai import OpenAI

from sepsis_atlas.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL


_CHAR_BUDGET = 2000
_OVERLAP_CHARS = 256
_BATCH_SIZE = 100
_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*?)\s*$")
_PAGE_COMMENT_RE = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _render_docling_json_to_markdown(payload: dict) -> str:
    """Synthesize markdown from a Docling JSON dump.

    Each section becomes ``"#" * level + heading``; the section's body
    follows. Each section is preceded by a ``<!-- page: N -->`` comment so
    the chunker can recover an approximate page number.
    """
    out: list[str] = []
    for sec in payload.get("sections") or []:
        page = sec.get("page")
        level = max(1, min(int(sec.get("level") or 1), 3))
        heading = (sec.get("heading") or "").strip()
        body = (sec.get("text") or "").strip()
        if page is not None:
            out.append(f"<!-- page: {page} -->")
        if heading:
            out.append(f"{'#' * level} {heading}")
        if body:
            out.append(body)
        out.append("")
    if not out:
        full = (payload.get("full_text") or "").strip()
        if full:
            out.append(full)
    return "\n".join(out).strip() + "\n"


def _load_paper_markdown(path: Path) -> str:
    if path.suffix == ".md":
        return path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        return _render_docling_json_to_markdown(payload)
    raise ValueError(f"unsupported paper file extension: {path}")


def _split_into_sections(md: str) -> list[dict]:
    """Walk markdown line-by-line, emitting sections demarcated by ATX headings.

    Each section dict has ``heading`` (the most recent ATX heading or None),
    ``page`` (last seen page comment, may be None), and ``body`` (the prose
    between this heading and the next).
    """
    sections: list[dict] = []
    current = {"heading": None, "page": None, "body_lines": []}
    last_page: int | None = None

    def flush() -> None:
        body = "\n".join(current["body_lines"]).strip()
        if current["heading"] or body:
            sections.append(
                {
                    "heading": current["heading"],
                    "page": current["page"],
                    "body": body,
                }
            )

    for line in md.splitlines():
        page_match = _PAGE_COMMENT_RE.search(line)
        if page_match:
            last_page = int(page_match.group(1))
            continue
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush()
            current = {
                "heading": heading_match.group(2).strip() or None,
                "page": last_page,
                "body_lines": [],
            }
        else:
            current["body_lines"].append(line)
    flush()
    return sections


def _sliding_chunks(body: str) -> list[str]:
    if len(body) <= _CHAR_BUDGET:
        return [body] if body.strip() else []
    out: list[str] = []
    step = _CHAR_BUDGET - _OVERLAP_CHARS
    for start in range(0, len(body), step):
        piece = body[start : start + _CHAR_BUDGET]
        if piece.strip():
            out.append(piece)
        if start + _CHAR_BUDGET >= len(body):
            break
    return out


def _chunk_paper(paper_file_name: str, md: str) -> list[dict]:
    chunks: list[dict] = []
    idx = 0
    for sec in _split_into_sections(md):
        body = sec["body"]
        if not body.strip():
            continue
        for piece in _sliding_chunks(body):
            chunks.append(
                {
                    "paper_file_name": paper_file_name,
                    "section_heading": sec["heading"],
                    "page_estimate": sec["page"],
                    "snippet": piece.strip(),
                    "chunk_id": f"{paper_file_name}#{idx}",
                }
            )
            idx += 1
    return chunks


class KGTextIndex:
    def __init__(
        self,
        papers_dir: Path = Path("data/papers/parsed"),
        cache_path: Path = Path("runs/embeddings.npz"),
        model: str = "openai/text-embedding-3-large",
        dim: int = 3072,
    ) -> None:
        self.papers_dir = Path(papers_dir)
        self.cache_path = Path(cache_path)
        self.model = model
        self.dim = dim

        self._client: OpenAI | None = None
        self._vectors: np.ndarray | None = None
        self._chunk_ids: list[str] = []
        self._metadata: list[dict] = []
        self._paper_hashes: dict[str, str] = {}
        self._tokens_in: int = 0

    @property
    def n_chunks(self) -> int:
        return 0 if self._vectors is None else int(self._vectors.shape[0])

    @property
    def tokens_in(self) -> int:
        return self._tokens_in

    def _get_client(self) -> OpenAI:
        if self._client is None:
            if not OPENROUTER_API_KEY:
                raise RuntimeError(
                    "OPENROUTER_API_KEY is not set; cannot embed text via OpenRouter."
                )
            self._client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
        return self._client

    def _list_paper_paths(self) -> list[Path]:
        if not self.papers_dir.exists():
            return []
        md_paths = sorted(self.papers_dir.glob("*.md"))
        json_paths = sorted(self.papers_dir.glob("*.json"))
        seen: set[str] = set()
        out: list[Path] = []
        for p in md_paths + json_paths:
            if p.stem in seen:
                continue
            seen.add(p.stem)
            out.append(p)
        return out

    def _embed(self, texts: list[str]) -> np.ndarray:
        client = self._get_client()
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i : i + _BATCH_SIZE]
            try:
                resp = client.embeddings.create(model=self.model, input=batch)
            except Exception as e:
                raise RuntimeError(
                    f"OpenRouter embeddings call failed for model={self.model!r}: {e}"
                ) from e
            usage = getattr(resp, "usage", None)
            if usage is not None:
                self._tokens_in += int(getattr(usage, "prompt_tokens", 0) or 0)
            for j, item in enumerate(resp.data):
                vec = np.asarray(item.embedding, dtype=np.float32)
                if vec.shape[0] != self.dim:
                    raise RuntimeError(
                        f"embedding dim mismatch: expected {self.dim}, got {vec.shape[0]}"
                    )
                out[i + j] = vec
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        return (out / norms).astype(np.float32)

    def _load_cache(self) -> tuple[np.ndarray, list[str], list[dict], dict[str, str]] | None:
        if not self.cache_path.exists():
            return None
        with np.load(self.cache_path, allow_pickle=True) as npz:
            vectors = np.asarray(npz["vectors"], dtype=np.float32)
            chunk_ids = [str(x) for x in npz["chunk_ids"].tolist()]
            metadata = [dict(x) for x in npz["metadata"].tolist()]
            paper_hashes = json.loads(str(npz["paper_hashes"].item()))
        return vectors, chunk_ids, metadata, paper_hashes

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        empty = np.zeros((0, self.dim), dtype=np.float32)
        buf = io.BytesIO()
        np.savez_compressed(
            buf,
            vectors=self._vectors if self._vectors is not None else empty,
            chunk_ids=np.array(self._chunk_ids, dtype=object),
            metadata=np.array(self._metadata, dtype=object),
            paper_hashes=np.array(json.dumps(self._paper_hashes), dtype=object),
        )
        self.cache_path.write_bytes(buf.getvalue())

    def build_or_load(self) -> None:
        cached = self._load_cache()
        cached_vectors: np.ndarray | None = None
        cached_chunk_ids: list[str] = []
        cached_metadata: list[dict] = []
        cached_hashes: dict[str, str] = {}
        if cached is not None:
            cached_vectors, cached_chunk_ids, cached_metadata, cached_hashes = cached

        paper_paths = self._list_paper_paths()
        if not paper_paths:
            self._vectors = np.zeros((0, self.dim), dtype=np.float32)
            self._chunk_ids = []
            self._metadata = []
            self._paper_hashes = {}
            self._save_cache()
            return

        new_vectors_blocks: list[np.ndarray] = []
        new_chunk_ids: list[str] = []
        new_metadata: list[dict] = []
        new_hashes: dict[str, str] = {}

        for path in paper_paths:
            paper_file_name = path.stem
            md = _load_paper_markdown(path)
            paper_hash = _sha256(md)
            new_hashes[paper_file_name] = paper_hash

            if cached_hashes.get(paper_file_name) == paper_hash and cached_vectors is not None:
                rows = [
                    i
                    for i, m in enumerate(cached_metadata)
                    if m.get("paper_file_name") == paper_file_name
                ]
                if rows:
                    new_vectors_blocks.append(cached_vectors[rows])
                    for r in rows:
                        new_chunk_ids.append(cached_chunk_ids[r])
                        new_metadata.append(cached_metadata[r])
                    continue

            chunks = _chunk_paper(paper_file_name, md)
            if not chunks:
                continue
            texts = [c["snippet"] for c in chunks]
            vecs = self._embed(texts)
            new_vectors_blocks.append(vecs)
            for c in chunks:
                new_chunk_ids.append(c["chunk_id"])
                new_metadata.append(
                    {
                        "paper_file_name": c["paper_file_name"],
                        "section_heading": c["section_heading"],
                        "page_estimate": c["page_estimate"],
                        "snippet": c["snippet"],
                    }
                )

        if new_vectors_blocks:
            self._vectors = np.vstack(new_vectors_blocks).astype(np.float32)
        else:
            self._vectors = np.zeros((0, self.dim), dtype=np.float32)
        self._chunk_ids = new_chunk_ids
        self._metadata = new_metadata
        self._paper_hashes = new_hashes
        self._save_cache()

    def search(self, query: str, paper_file_name: str | None = None, k: int = 5) -> list[dict]:
        if self._vectors is None or self._vectors.shape[0] == 0:
            return []
        q_vec = self._embed([query])[0]

        if paper_file_name is not None:
            mask = np.array(
                [m.get("paper_file_name") == paper_file_name for m in self._metadata],
                dtype=bool,
            )
            if not mask.any():
                return []
            sub = self._vectors[mask]
            sims = sub @ q_vec
            idxs_local = np.argsort(-sims)[:k]
            global_indices = np.flatnonzero(mask)[idxs_local]
            scores = sims[idxs_local]
        else:
            sims = self._vectors @ q_vec
            idxs = np.argsort(-sims)[:k]
            global_indices = idxs
            scores = sims[idxs]

        results: list[dict] = []
        for rank, gi in enumerate(global_indices):
            meta = self._metadata[int(gi)]
            results.append(
                {
                    "paper_file_name": meta.get("paper_file_name"),
                    "section_heading": meta.get("section_heading"),
                    "page_estimate": meta.get("page_estimate"),
                    "snippet": meta.get("snippet"),
                    "score": float(np.clip((scores[rank] + 1.0) / 2.0, 0.0, 1.0)),
                    "chunk_id": self._chunk_ids[int(gi)],
                }
            )
        return results

    @classmethod
    def from_default_corpus(
        cls,
        papers_dir: Path = Path("data/papers/parsed"),
        cache_path: Path = Path("runs/embeddings.npz"),
    ) -> "KGTextIndex":
        """Build-or-load against the default parsed-papers dir + embedding cache.

        Embedding failures (e.g. no OpenRouter key in CI) are swallowed so the
        API stays import-safe; ``search`` then returns ``[]``.
        """
        idx = cls(papers_dir=papers_dir, cache_path=cache_path)
        try:
            idx.build_or_load()
        except Exception as e:
            # Loud-fail-soft: log so operators notice that search_text will
            # silently return [] for the rest of the process lifetime.
            print(
                f"[kg_text_index] from_default_corpus failed; index empty: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
        return idx

    def chunks_for_paper(self, paper_file_name: str) -> list[dict]:
        path_md = self.papers_dir / f"{paper_file_name}.md"
        path_json = self.papers_dir / f"{paper_file_name}.json"
        if path_md.exists():
            md = _load_paper_markdown(path_md)
        elif path_json.exists():
            md = _load_paper_markdown(path_json)
        else:
            raise FileNotFoundError(
                f"no parsed paper found for {paper_file_name!r} in {self.papers_dir}"
            )
        return _chunk_paper(paper_file_name, md)


__all__ = ["KGTextIndex"]
