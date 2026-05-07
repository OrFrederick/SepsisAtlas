"""LangChain-compatible Neo4j knowledge-graph tools for the agent loop.

Exposes six uniform `@tool`-decorated callables via `KGTools.all()`. Each tool
returns a `ToolResult` envelope with `nodes`, `edges`, and a one-line `summary`
that the agent often surfaces verbatim. Numbers come from Neo4j; the LLM is
never asked to compute or invent data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional, TypedDict

from langchain_core.tools import tool


if TYPE_CHECKING:
    from api.backends.kg_store import KGStore
    from api.backends.kg_text_index import KGTextIndex


class ToolResult(TypedDict):
    nodes: list[dict]
    edges: list[dict]
    summary: str


_VALID_NODE_TYPES = {
    "Paper",
    "Cohort",
    "PredictorModel",
    "Section",
    "PaperTable",
    "Figure",
    "Reference",
}

_VALID_EDGE_KINDS = {
    "HAS_COHORT",
    "REPORTS",
    "HAS_SECTION",
    "HAS_TABLE",
    "HAS_FIGURE",
    "HAS_REFERENCE",
    "CITES",
    "MENTIONS_PM",
    "MENTIONS_COHORT",
}

_PK_BY_TYPE = {
    "Paper": "file_name",
    "Cohort": "cohort_id",
    "PredictorModel": "id",
    "Section": "section_id",
    "PaperTable": "table_id",
    "Figure": "figure_id",
    "Reference": "ref_id",
}

_ARRAY_FIELDS = {"predictors_canonical", "outcomes_covered"}


def _node_pk_value(node: dict) -> str | None:
    for label in ("file_name", "cohort_id", "id", "section_id", "table_id", "figure_id", "ref_id"):
        v = node.get(label)
        if v is not None:
            return str(v)
    return None


def _node_id_field(node_type: str) -> str:
    return _PK_BY_TYPE[node_type]


def _record_node(record_value: Any) -> dict:
    if isinstance(record_value, dict):
        return dict(record_value)
    try:
        return dict(record_value)
    except (TypeError, ValueError):
        return {"value": record_value}


class KGTools:
    def __init__(self, store: "KGStore", text_index: "KGTextIndex") -> None:
        self.store = store
        self.text_index = text_index

    # ------------------------------------------------------------------
    # Internal implementations (callable directly by tests)
    # ------------------------------------------------------------------

    def _find(self, node_type: str, **filters: Any) -> ToolResult:
        if node_type not in _VALID_NODE_TYPES:
            return {
                "nodes": [],
                "edges": [],
                "summary": f"find: unknown node_type {node_type!r}",
            }

        where_parts: list[str] = []
        params: dict[str, Any] = {}
        i = 0
        for key, value in filters.items():
            if value is None:
                continue
            if key.endswith("_contains") and key[: -len("_contains")] in _ARRAY_FIELDS:
                base = key[: -len("_contains")]
                pname = f"p{i}"
                where_parts.append(f"$" + pname + f" IN n.{base}")
                params[pname] = value
                i += 1
                continue
            if key.endswith("_contains"):
                base = key[: -len("_contains")]
                pname = f"p{i}"
                where_parts.append(f"$" + pname + f" IN n.{base}")
                params[pname] = value
                i += 1
                continue
            if isinstance(value, list):
                pname = f"p{i}"
                where_parts.append(f"n.{key} IN ${pname}")
                params[pname] = value
                i += 1
            elif isinstance(value, str) and value.startswith("~"):
                pname = f"p{i}"
                where_parts.append(f"toLower(toString(n.{key})) CONTAINS toLower(${pname})")
                params[pname] = value[1:]
                i += 1
            else:
                pname = f"p{i}"
                where_parts.append(f"n.{key} = ${pname}")
                params[pname] = value
                i += 1

        where_clause = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cypher = f"MATCH (n:{node_type}) {where_clause} RETURN n LIMIT 100"
        rows = self.store.run(cypher, **params)
        nodes = [_record_node(r["n"]) for r in rows if "n" in r]
        for n, label in zip(nodes, [node_type] * len(nodes)):
            n.setdefault("node_type", label)
        return {
            "nodes": nodes,
            "edges": [],
            "summary": f"find: {len(nodes)} {node_type} nodes match",
        }

    def _infer_type_and_pk(self, node_id: str, node_type: str | None) -> tuple[str, str] | None:
        if node_type is not None:
            if node_type not in _PK_BY_TYPE:
                return None
            return node_type, _PK_BY_TYPE[node_type]
        for nt, pk in _PK_BY_TYPE.items():
            rows = self.store.run(
                f"MATCH (n:{nt}) WHERE n.{pk} = $id RETURN n LIMIT 1",
                id=node_id,
            )
            if rows:
                return nt, pk
        return None

    def _expand(
        self,
        node_id: str,
        hops: int = 1,
        edge_kind: str | None = None,
        node_type: str | None = None,
    ) -> ToolResult:
        if hops not in (1, 2):
            return {
                "nodes": [],
                "edges": [],
                "summary": f"expand: hops must be 1 or 2 (got {hops})",
            }
        inferred = self._infer_type_and_pk(node_id, node_type)
        if inferred is None:
            return {
                "nodes": [],
                "edges": [],
                "summary": f"expand: no node found with id {node_id!r}",
            }
        nt, pk = inferred
        rel_filter = f":{edge_kind}" if edge_kind else ""
        if edge_kind is not None and edge_kind not in _VALID_EDGE_KINDS:
            return {
                "nodes": [],
                "edges": [],
                "summary": f"expand: unknown edge_kind {edge_kind!r}",
            }
        cypher = (
            f"MATCH (start:{nt}) WHERE start.{pk} = $id "
            f"OPTIONAL MATCH path = (start)-[r{rel_filter}*1..{hops}]-(other) "
            f"RETURN start, path"
        )

        nodes_by_id: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()

        # Single Cypher round trip; we need the live driver session because
        # `path.relationships` is only available on the raw record objects, not
        # on the dict-of-dicts that `KGStore.run` returns.
        with self.store.driver().session(database=self.store._database) as session:  # type: ignore[attr-defined]
            result = session.run(cypher, id=node_id)
            for record in result:
                start = record["start"]
                path = record["path"]
                if start is not None:
                    n = dict(start)
                    sid = _node_pk_value(n) or node_id
                    nodes_by_id.setdefault(sid, n)
                if path is None:
                    continue
                for node in path.nodes:
                    n = dict(node)
                    nid = _node_pk_value(n)
                    if nid:
                        nodes_by_id.setdefault(nid, n)
                for rel in path.relationships:
                    src = dict(rel.start_node)
                    dst = dict(rel.end_node)
                    src_id = _node_pk_value(src)
                    dst_id = _node_pk_value(dst)
                    if src_id is None or dst_id is None:
                        continue
                    key = (src_id, rel.type, dst_id)
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    edges.append(
                        {
                            "src_id": src_id,
                            "type": rel.type,
                            "dst_id": dst_id,
                            "attrs": dict(rel),
                        }
                    )

        nodes = list(nodes_by_id.values())
        return {
            "nodes": nodes,
            "edges": edges,
            "summary": f"expand: {len(nodes)} nodes, {len(edges)} edges from {node_id}",
        }

    def _get(self, node_id: str, node_type: str | None = None) -> ToolResult:
        inferred = self._infer_type_and_pk(node_id, node_type)
        if inferred is None:
            return {
                "nodes": [],
                "edges": [],
                "summary": f"get: no node found with id {node_id!r}",
            }
        nt, pk = inferred
        cypher = (
            f"MATCH (n:{nt}) WHERE n.{pk} = $id "
            f"OPTIONAL MATCH (n)-[r]-(m) "
            f"RETURN n, r, m"
        )

        nodes_by_id: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set[tuple[str, str, str]] = set()

        with self.store.driver().session(database=self.store._database) as session:  # type: ignore[attr-defined]
            result = session.run(cypher, id=node_id)
            for record in result:
                n = record["n"]
                if n is not None:
                    nd = dict(n)
                    nid = _node_pk_value(nd) or node_id
                    nodes_by_id.setdefault(nid, nd)
                m = record["m"]
                r = record["r"]
                if m is not None:
                    md = dict(m)
                    mid = _node_pk_value(md)
                    if mid:
                        nodes_by_id.setdefault(mid, md)
                if r is not None:
                    src = dict(r.start_node)
                    dst = dict(r.end_node)
                    src_id = _node_pk_value(src)
                    dst_id = _node_pk_value(dst)
                    if src_id is None or dst_id is None:
                        continue
                    key = (src_id, r.type, dst_id)
                    if key in seen_edges:
                        continue
                    seen_edges.add(key)
                    edges.append(
                        {
                            "src_id": src_id,
                            "type": r.type,
                            "dst_id": dst_id,
                            "attrs": dict(r),
                        }
                    )

        nodes = list(nodes_by_id.values())
        return {
            "nodes": nodes,
            "edges": edges,
            "summary": f"get: {len(nodes)} nodes, {len(edges)} edges around {node_id}",
        }

    def _search_text(
        self,
        query: str,
        paper_file_name: str | None = None,
        k: int = 5,
    ) -> ToolResult:
        hits = self.text_index.search(query, paper_file_name=paper_file_name, k=k)
        nodes: list[dict] = []
        for h in hits:
            nodes.append(
                {
                    "node_type": "Chunk",
                    "chunk_id": h.get("chunk_id"),
                    "paper_file_name": h.get("paper_file_name"),
                    "section_heading": h.get("section_heading"),
                    "page_estimate": h.get("page_estimate"),
                    "snippet": h.get("snippet"),
                    "score": h.get("score"),
                }
            )
        n_total = getattr(self.text_index, "n_chunks", len(nodes))
        return {
            "nodes": nodes,
            "edges": [],
            "summary": f"search_text: top {len(nodes)} of {n_total} chunks for {query!r}",
        }

    def _pool_effects(
        self,
        predictor_model_ids: list[str],
        effect_type: str = "OR",
    ) -> ToolResult:
        if not predictor_model_ids:
            return {
                "nodes": [],
                "edges": [],
                "summary": "pool_effects: no PredictorModel ids supplied",
            }
        rows = self.store.run(
            "MATCH (pm:PredictorModel) WHERE pm.id IN $ids RETURN pm",
            ids=list(predictor_model_ids),
        )
        pm_nodes = [_record_node(r["pm"]) for r in rows if "pm" in r]
        target = (effect_type or "").strip().upper()
        filtered = [
            n
            for n in pm_nodes
            if (n.get("effect_type") or "").strip().upper() == target
        ]

        if len(filtered) < 2:
            return {
                "nodes": pm_nodes,
                "edges": [],
                "summary": "pool_effects: insufficient rows (k<2)",
            }

        from stats.meta import pool

        pooled = pool(filtered, method="random_effects")
        pooled_estimate = pooled.get("pooled_estimate_exp")
        if pooled_estimate is None:
            return {
                "nodes": pm_nodes,
                "edges": [],
                "summary": "pool_effects: pooling failed (no estimate)",
            }
        ci_lo = pooled.get("ci_lo")
        ci_hi = pooled.get("ci_hi")
        i2_raw = pooled.get("i_squared")
        i2_frac = (i2_raw / 100.0) if (i2_raw is not None and i2_raw > 1.0) else (i2_raw or 0.0)
        tau2 = pooled.get("tau_squared")
        k = pooled.get("n_studies", len(filtered))

        synthetic = {
            "node_type": "Pooled",
            "pooled": pooled_estimate,
            "ci_lo": ci_lo,
            "ci_hi": ci_hi,
            "tau2": tau2,
            "k": k,
            "i2": i2_frac,
            "effect_type": target,
        }

        ci_lo_str = f"{ci_lo:.2f}" if ci_lo is not None else "?"
        ci_hi_str = f"{ci_hi:.2f}" if ci_hi is not None else "?"
        summary = (
            f"pool_effects: pooled {target} {pooled_estimate:.2f} "
            f"({ci_lo_str}-{ci_hi_str}), k={k}, "
            f"I²={i2_frac:.0%}"
        )
        return {
            "nodes": [synthetic, *pm_nodes],
            "edges": [],
            "summary": summary,
        }

    def _project_table(
        self,
        shape: str,
        predictor_model_ids: list[str] | None = None,
    ) -> ToolResult:
        if shape == "evidence":
            return self._project_evidence(predictor_model_ids or [])
        if shape == "ranked_predictors":
            return self._project_ranked_predictors(predictor_model_ids)
        if shape == "study_summary":
            return self._project_study_summary()
        return {
            "nodes": [],
            "edges": [],
            "summary": f"project_table: unknown shape {shape!r}",
        }

    def _project_evidence(self, pm_ids: list[str]) -> ToolResult:
        columns = [
            "#",
            "Study",
            "Population",
            "N",
            "Predictor",
            "Outcome",
            "Timing",
            "Method",
            "Adjustment",
            "Effect Size",
            "Performance",
            "Co-tested",
            "Pop Relevance",
            "Verifier",
            "Source",
        ]
        if not pm_ids:
            return {
                "nodes": [],
                "edges": [],
                "summary": " | ".join(columns),
            }

        cypher = """
        MATCH (pm:PredictorModel) WHERE pm.id IN $ids
        OPTIONAL MATCH (c:Cohort)-[:REPORTS]->(pm)
        OPTIONAL MATCH (p:Paper)-[:HAS_COHORT]->(c)
        OPTIONAL MATCH (c)-[:REPORTS]->(sib:PredictorModel) WHERE sib.id <> pm.id
        WITH pm, c, p, collect(DISTINCT sib.predictor_canonical) AS co_tested
        RETURN pm, c, p, co_tested
        """
        with self.store.driver().session(database=self.store._database) as session:  # type: ignore[attr-defined]
            result = session.run(cypher, ids=list(pm_ids))
            records = [dict(record) for record in result]

        rows: list[dict] = []
        for i, rec in enumerate(records, start=1):
            pm = dict(rec["pm"]) if rec.get("pm") is not None else {}
            cohort = dict(rec["c"]) if rec.get("c") is not None else {}
            paper = dict(rec["p"]) if rec.get("p") is not None else {}
            co_tested = [x for x in (rec.get("co_tested") or []) if x]

            study = paper.get("paper_ref") or paper.get("file_name") or pm.get("paper_file_name")
            n_val = cohort.get("cohort_size_n")
            effect_str = pm.get("effect_size_str")
            if not effect_str and pm.get("effect_value") is not None:
                ev = pm.get("effect_value")
                lo = pm.get("ci_lo")
                hi = pm.get("ci_hi")
                et = pm.get("effect_type") or ""
                if lo is not None and hi is not None:
                    effect_str = f"{et} {ev:.2f} ({lo:.2f}-{hi:.2f})"
                else:
                    effect_str = f"{et} {ev:.2f}"
            performance = None
            if pm.get("auc") is not None:
                lo = pm.get("auc_ci_lo")
                hi = pm.get("auc_ci_hi")
                if lo is not None and hi is not None:
                    performance = f"AUC {pm['auc']:.2f} ({lo:.2f}-{hi:.2f})"
                else:
                    performance = f"AUC {pm['auc']:.2f}"

            paper_fn = pm.get("paper_file_name") or paper.get("file_name")
            anchor_page = pm.get("anchor_page")
            source = None
            if paper_fn and anchor_page is not None:
                # anchor_bbox is stored as a JSON-encoded 4-float list (see
                # kg_predictor_extractor.py); decode + re-format as plain
                # comma-floats so the viewer URL is well-formed.
                bbox_q = ""
                bbox_raw = pm.get("anchor_bbox")
                if bbox_raw:
                    try:
                        import json as _json

                        vals = _json.loads(bbox_raw) if isinstance(bbox_raw, str) else bbox_raw
                        if isinstance(vals, list) and len(vals) == 4:
                            bbox_q = f"&bbox={','.join(f'{float(v):.2f}' for v in vals)}&origin=tl"
                    except (ValueError, TypeError):
                        pass
                src_url = f"/viewer/{paper_fn}?page={anchor_page}{bbox_q}"
                source = f"[{paper_fn} p.{anchor_page}]({src_url})"

            row = {
                "#": i,
                "study": study,
                "population": cohort.get("population_description"),
                "n": n_val,
                "predictor": pm.get("predictor_canonical") or pm.get("predictors"),
                "outcome": pm.get("outcome"),
                "timing": pm.get("timing"),
                "method": pm.get("model_specification"),
                "adjustment": pm.get("adjustment_kind"),
                "effect_size": effect_str,
                "performance": performance,
                "co_tested": ", ".join(sorted(co_tested)) if co_tested else None,
                "pop_relevance": None,
                "verifier": pm.get("verifier_verdict"),
                "source": source,
            }
            rows.append(row)

        return {
            "nodes": rows,
            "edges": [],
            "summary": " | ".join(columns),
        }

    def _project_ranked_predictors(self, pm_ids: list[str] | None) -> ToolResult:
        columns = ["Predictor", "Best Metric", "Value", "Studies", "Notes"]
        if pm_ids:
            cypher = "MATCH (pm:PredictorModel) WHERE pm.id IN $ids RETURN pm"
            rows_raw = self.store.run(cypher, ids=list(pm_ids))
        else:
            rows_raw = self.store.run("MATCH (pm:PredictorModel) RETURN pm LIMIT 5000")
        pms = [_record_node(r["pm"]) for r in rows_raw if "pm" in r]

        groups: dict[str, list[dict]] = {}
        for pm in pms:
            key = pm.get("predictor_canonical") or pm.get("predictors") or "unknown"
            groups.setdefault(key, []).append(pm)

        out_rows: list[dict] = []
        for predictor, group in sorted(groups.items()):
            aucs = [g.get("auc") for g in group if g.get("auc") is not None]
            if aucs:
                best_metric = "AUC"
                value = max(aucs)
            else:
                import math

                best_log = None
                best_val = None
                best_et = None
                for g in group:
                    et = (g.get("effect_type") or "").strip().upper()
                    ev = g.get("effect_value")
                    if et and ev is not None:
                        try:
                            log_v = abs(math.log(float(ev)))
                        except (ValueError, TypeError):
                            continue
                        if best_log is None or log_v > best_log:
                            best_log = log_v
                            best_val = float(ev)
                            best_et = et
                best_metric = best_et
                value = best_val

            studies = sorted({g.get("paper_file_name") for g in group if g.get("paper_file_name")})
            notes_parts = []
            outcome_types = sorted(
                {(g.get("outcome_type") or "").strip() for g in group if g.get("outcome_type")}
            )
            if outcome_types:
                notes_parts.append(",".join(outcome_types))
            verdicts = [g.get("verifier_verdict") for g in group if g.get("verifier_verdict")]
            if verdicts:
                from collections import Counter

                top = Counter(verdicts).most_common(1)[0][0]
                notes_parts.append(f"verifier:{top}")

            out_rows.append(
                {
                    "predictor": predictor,
                    "best_metric": best_metric,
                    "value": value,
                    "studies": len(studies),
                    "notes": "; ".join(notes_parts) if notes_parts else None,
                }
            )

        out_rows.sort(
            key=lambda r: (-(r["value"] or 0.0) if r.get("best_metric") == "AUC" else 0, r["predictor"])
        )
        return {
            "nodes": out_rows,
            "edges": [],
            "summary": " | ".join(columns),
        }

    def _project_study_summary(self) -> ToolResult:
        columns = ["Study", "Year", "Cohorts", "N (total)", "Predictors", "Outcomes", "Source"]
        cypher = """
        MATCH (p:Paper)
        OPTIONAL MATCH (p)-[:HAS_COHORT]->(c:Cohort)
        OPTIONAL MATCH (c)-[:REPORTS]->(pm:PredictorModel)
        WITH p,
             count(DISTINCT c) AS n_cohorts,
             sum(coalesce(c.cohort_size_n, 0)) AS n_total,
             collect(DISTINCT pm.predictor_canonical) AS preds,
             collect(DISTINCT pm.outcome_type) AS outcomes
        RETURN p, n_cohorts, n_total, preds, outcomes
        """
        records = self.store.run(cypher)
        rows: list[dict] = []
        for rec in records:
            paper = _record_node(rec["p"])
            preds = [x for x in (rec.get("preds") or []) if x]
            outcomes = [x for x in (rec.get("outcomes") or []) if x]
            file_name = paper.get("file_name")
            source = f"/viewer/{file_name}" if file_name else None
            rows.append(
                {
                    "study": paper.get("paper_ref") or file_name,
                    "year": paper.get("year"),
                    "cohorts": rec.get("n_cohorts") or 0,
                    "n_total_": rec.get("n_total") or 0,
                    "predictors": ", ".join(sorted(preds)) if preds else None,
                    "outcomes": ", ".join(sorted(outcomes)) if outcomes else None,
                    "source": source,
                }
            )
        return {
            "nodes": rows,
            "edges": [],
            "summary": " | ".join(columns),
        }

    # ------------------------------------------------------------------
    # LangChain tool surface
    # ------------------------------------------------------------------

    def all(self) -> list:
        kg = self

        def _safe(name: str, fn):
            """Wrap a tool body so Cypher / driver exceptions become a tool
            result with empty nodes + an error ``summary`` instead of bubbling
            up through LangGraph and aborting the whole agent run."""

            def wrapper(*args, **kwargs):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    return {
                        "nodes": [],
                        "edges": [],
                        "summary": f"{name}: tool error — {type(e).__name__}: {str(e)[:200]}",
                    }

            return wrapper

        @tool
        def find(node_type: str, filters: Optional[dict] = None) -> dict:
            """Find nodes of a given type matching filter criteria.

            Args:
                node_type: One of "Paper", "Cohort", "PredictorModel", "Section",
                    "PaperTable", "Figure", "Reference".
                filters: Mapping of property name to value. Values may be scalars
                    (exact match), lists (IN match), or strings starting with "~"
                    for case-insensitive substring match. For array-valued
                    properties on Paper/Cohort (e.g. `predictors_canonical`), use
                    a `<name>_contains` key, e.g.
                    `{"predictors_canonical_contains": "SOFA"}`.

            Returns a ToolResult with up to 100 matching nodes, no edges, and a
            summary like "find: 3 PredictorModel nodes match".
            """
            return _safe("find", kg._find)(node_type, **(filters or {}))

        @tool
        def expand(
            node_id: str,
            hops: int = 1,
            edge_kind: Optional[str] = None,
            node_type: Optional[str] = None,
        ) -> dict:
            """Expand a node's neighborhood out to N hops along the graph.

            Args:
                node_id: Primary key value of the start node (file_name for
                    Paper, cohort_id for Cohort, id for PredictorModel, etc.).
                hops: 1 or 2. Walks variable-length paths from start to end.
                node_type: Optional explicit label of the start node. If
                    omitted, inferred from which constraint matches `node_id`.
                edge_kind: Restrict traversal to one relationship type
                    (HAS_COHORT, REPORTS, HAS_SECTION, HAS_TABLE, HAS_FIGURE,
                    HAS_REFERENCE, CITES, MENTIONS_PM, MENTIONS_COHORT). None
                    means all edge types.

            Returns a ToolResult with reachable nodes plus the edges traversed.
            """
            return _safe("expand", kg._expand)(
                node_id, hops=hops, edge_kind=edge_kind, node_type=node_type
            )

        @tool
        def get(node_id: str, node_type: Optional[str] = None) -> dict:
            """Fetch one node plus all its 1-hop neighbors and the edges between them.

            The "tell me everything about X" tool. If `node_type` is None, the
            label is inferred from which constraint matches `node_id`.

            Args:
                node_id: Primary key value of the node.
                node_type: Optional explicit label.

            Returns a ToolResult with the node, neighbors, and edges.
            """
            return _safe("get", kg._get)(node_id, node_type=node_type)

        @tool
        def search_text(
            query: str,
            paper_file_name: Optional[str] = None,
            k: int = 5,
        ) -> dict:
            """Embedding-backed full-text search over parsed paper prose.

            Args:
                query: Natural language query.
                paper_file_name: Optional restriction to one paper's chunks.
                k: Top-K results to return (default 5).

            Returns a ToolResult whose `nodes` are synthetic Chunk nodes with
            chunk_id, paper_file_name, section_heading, page_estimate, snippet,
            and score. No edges.
            """
            return _safe("search_text", kg._search_text)(
                query, paper_file_name=paper_file_name, k=k
            )

        @tool
        def pool_effects(
            predictor_model_ids: list[str],
            effect_type: str = "OR",
        ) -> dict:
            """DerSimonian-Laird random-effects meta-analysis of PredictorModel rows.

            Args:
                predictor_model_ids: List of PredictorModel.id values to pool.
                effect_type: "OR" (default), "HR", or "RR". Rows whose
                    effect_type does not match are dropped before pooling.

            Returns a ToolResult whose `nodes` start with one synthetic Pooled
            node ({pooled, ci_lo, ci_hi, tau2, k, i2}) followed by the input
            PredictorModel rows for traceability. If fewer than 2 rows survive
            the filter, returns the rows unchanged with summary
            "pool_effects: insufficient rows (k<2)".
            """
            return _safe("pool_effects", kg._pool_effects)(
                predictor_model_ids, effect_type=effect_type
            )

        @tool
        def project_table(
            shape: str,
            predictor_model_ids: Optional[list[str]] = None,
        ) -> dict:
            """Render a structured evidence table for downstream display.

            Args:
                shape: One of "evidence" (14-column per-PM table),
                    "ranked_predictors" (one row per predictor_canonical), or
                    "study_summary" (one row per Paper).
                predictor_model_ids: Required for "evidence"; optional for
                    "ranked_predictors" (None = whole graph).

            Returns a ToolResult whose `nodes` are the table rows (one dict per
            row, lower-snake-case column keys) and whose `summary` is the
            column header line joined by " | ".
            """
            return _safe("project_table", kg._project_table)(
                shape, predictor_model_ids=predictor_model_ids
            )

        return [find, expand, get, search_text, pool_effects, project_table]


__all__ = ["KGTools", "ToolResult"]
