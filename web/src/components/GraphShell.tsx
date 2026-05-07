/*
  Graph view — force-directed canvas of the Paper / Cohort / PredictorModel
  subgraph plus a detail panel. Click any node to surface its attributes and
  (for PredictorModel nodes) a deep-link to the source PDF anchor.

  Why react-force-graph-2d: canvas-only, no DOM-per-node, handles ~5k nodes
  smoothly. Comparable d3-force libs need a per-node React component which
  blows up at this corpus size. shadcn / 21st.dev components plug into the
  detail panel cleanly.
*/

import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";

const BACKEND_URL = ((import.meta.env.PUBLIC_BACKEND_URL as string | undefined) || "").replace(
  /\/$/,
  "",
);

type NodeType = "Paper" | "Cohort" | "PredictorModel";

type RawNode = {
  id: string;
  type: NodeType;
  label: string;
  // Paper
  year?: number | null;
  n_cohorts?: number | null;
  n_predictor_models?: number | null;
  // Cohort
  paper?: string | null;
  n?: string | number | null;
  population?: string | null;
  // PredictorModel
  predictor?: string | null;
  outcome_type?: string | null;
  outcome?: string | null;
  effect?: string | null;
  cohort?: string | null;
  verdict?: string | null;
  page?: number | string | null;
  bbox?: number[] | string | null;
};

type RawEdge = {
  src: string;
  dst: string;
  kind: "HAS_COHORT" | "REPORTS";
};

type GraphPayload = {
  nodes: RawNode[];
  edges: RawEdge[];
  stats: { papers: number; cohorts: number; predictor_models: number; edges: number };
};

type GraphNode = RawNode & { x?: number; y?: number; vx?: number; vy?: number };
type GraphLink = { source: string; target: string; kind: RawEdge["kind"] };

const NODE_COLOR: Record<NodeType, string> = {
  Paper: "#ffd23f",
  Cohort: "#7cc5ff",
  PredictorModel: "#cfd2da",
};

const NODE_RADIUS: Record<NodeType, number> = {
  Paper: 7,
  Cohort: 5,
  PredictorModel: 3.5,
};

function parseBbox(bbox: unknown): number[] | null {
  if (bbox == null) return null;
  let arr: unknown = bbox;
  if (typeof arr === "string") {
    try {
      arr = JSON.parse(arr);
    } catch {
      return null;
    }
  }
  if (!Array.isArray(arr) || arr.length !== 4) return null;
  const nums = arr.map((x) => Number(x));
  if (nums.some((n) => !Number.isFinite(n))) return null;
  return nums;
}

function buildPmViewerUrl(node: RawNode): string | null {
  if (node.type !== "PredictorModel" || !node.paper) return null;
  const origin = BACKEND_URL || (typeof window !== "undefined" ? window.location.origin : "");
  let page = parseInt(String(node.page ?? ""), 10);
  if (!Number.isFinite(page) || page < 1) page = 1;
  let url = `${origin}/viewer/${encodeURIComponent(node.paper)}?page=${page}`;
  const bbox = parseBbox(node.bbox);
  if (bbox) {
    url += `&bbox=${bbox.map((v) => v.toFixed(2)).join(",")}&origin=tl`;
  }
  return url;
}

function NodeDetail({ node }: { node: RawNode | null }) {
  if (!node) {
    return (
      <div className="graph-detail">
        <div className="placeholder">
          Click any node to inspect its attributes. Yellow = paper, blue = cohort, grey =
          predictor model.
        </div>
      </div>
    );
  }

  const pairs: Array<[string, string]> = [];
  if (node.type === "Paper") {
    if (node.year != null) pairs.push(["Year", String(node.year)]);
    if (node.n_cohorts != null) pairs.push(["Cohorts", String(node.n_cohorts)]);
    if (node.n_predictor_models != null)
      pairs.push(["Predictors", String(node.n_predictor_models)]);
    pairs.push(["File", node.id]);
  } else if (node.type === "Cohort") {
    if (node.paper) pairs.push(["Paper", node.paper]);
    if (node.n != null && node.n !== "") pairs.push(["N", String(node.n)]);
    if (node.population) pairs.push(["Population", node.population]);
  } else {
    if (node.predictor) pairs.push(["Predictor", node.predictor]);
    if (node.outcome_type) pairs.push(["Outcome type", node.outcome_type]);
    if (node.outcome) pairs.push(["Outcome", node.outcome]);
    if (node.effect) pairs.push(["Effect", node.effect]);
    if (node.cohort) pairs.push(["Cohort", node.cohort]);
    if (node.paper) pairs.push(["Paper", node.paper]);
    if (node.verdict) pairs.push(["Verdict", node.verdict]);
    if (node.page != null) pairs.push(["Page", String(node.page)]);
  }

  const sourceUrl = buildPmViewerUrl(node);

  return (
    <div className="graph-detail">
      <h3>{node.label}</h3>
      <span className={`type-pill ${node.type}`}>{node.type}</span>
      <div className="kv-list">
        {pairs.map(([k, v]) => (
          <span key={k} style={{ display: "contents" }}>
            <span className="k">{k}</span>
            <span className="v">{v}</span>
          </span>
        ))}
      </div>
      {sourceUrl ? (
        <a className="source-link" href={sourceUrl} target="_blank" rel="noreferrer">
          Open source PDF →
        </a>
      ) : null}
    </div>
  );
}

export default function GraphShell() {
  const [data, setData] = useState<GraphPayload | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useState<RawNode | null>(null);
  const [size, setSize] = useState<{ w: number; h: number }>({ w: 800, h: 600 });
  const canvasRef = useRef<HTMLDivElement | null>(null);

  // Fetch graph JSON once on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const url = `${BACKEND_URL || ""}/kg/graph`;
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(`status ${resp.status}`);
        const json = (await resp.json()) as GraphPayload;
        if (!cancelled) setData(json);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "unknown error";
        if (!cancelled) setErr(msg);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Track canvas container size so the force graph fills it; ResizeObserver
  // keeps it correct when the window is resized.
  useEffect(() => {
    const el = canvasRef.current;
    if (!el) return;
    const update = () => {
      const r = el.getBoundingClientRect();
      setSize({ w: Math.max(200, r.width), h: Math.max(200, r.height) });
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const graph = useMemo(() => {
    if (!data) return { nodes: [], links: [] };
    const nodes: GraphNode[] = data.nodes.map((n) => ({ ...n }));
    const links: GraphLink[] = data.edges.map((e) => ({
      source: e.src,
      target: e.dst,
      kind: e.kind,
    }));
    return { nodes, links };
  }, [data]);

  const stats = data?.stats;

  return (
    <div className="graph-shell">
      <div className="graph-controls">
        <span className="graph-stats">
          {stats
            ? `${stats.papers} papers · ${stats.cohorts} cohorts · ${stats.predictor_models} predictor models · ${stats.edges} edges`
            : "loading graph..."}
        </span>
        <div className="graph-legend">
          <span>
            <span className="swatch" style={{ background: NODE_COLOR.Paper }} /> Paper
          </span>
          <span>
            <span className="swatch" style={{ background: NODE_COLOR.Cohort }} /> Cohort
          </span>
          <span>
            <span className="swatch" style={{ background: NODE_COLOR.PredictorModel }} />{" "}
            PredictorModel
          </span>
        </div>
      </div>
      <main className="graph-split">
        <section ref={canvasRef} className="graph-canvas">
          {err ? <div className="error">Failed to load /kg/graph: {err}</div> : null}
          {!err && !data ? <div className="empty">Loading graph...</div> : null}
          {data ? (
            <ForceGraph2D
              graphData={graph}
              width={size.w}
              height={size.h}
              backgroundColor="#0f1115"
              nodeRelSize={1}
              linkColor={() => "rgba(140,147,166,0.25)"}
              linkWidth={(l) => ((l as unknown as GraphLink).kind === "HAS_COHORT" ? 1.2 : 0.6)}
              onNodeClick={(n) => setSelected(n as unknown as RawNode)}
              onBackgroundClick={() => setSelected(null)}
              nodeCanvasObject={(node, ctx, scale) => {
                const n = node as unknown as GraphNode;
                const r = NODE_RADIUS[n.type] / Math.max(0.3, Math.sqrt(scale));
                ctx.beginPath();
                ctx.arc(n.x ?? 0, n.y ?? 0, r * Math.sqrt(scale), 0, 2 * Math.PI);
                ctx.fillStyle = NODE_COLOR[n.type];
                ctx.fill();
                if (selected && selected.id === n.id) {
                  ctx.lineWidth = 2 / scale;
                  ctx.strokeStyle = "#ffffff";
                  ctx.stroke();
                }
                if (n.type === "Paper" && scale > 1.2) {
                  ctx.font = `${10 / scale}px ui-sans-serif, system-ui`;
                  ctx.fillStyle = "#cfd2da";
                  ctx.textAlign = "center";
                  ctx.textBaseline = "top";
                  ctx.fillText(n.label, n.x ?? 0, (n.y ?? 0) + r * Math.sqrt(scale) + 2);
                }
              }}
              nodePointerAreaPaint={(node, color, ctx) => {
                const n = node as unknown as GraphNode;
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(n.x ?? 0, n.y ?? 0, NODE_RADIUS[n.type] + 2, 0, 2 * Math.PI);
                ctx.fill();
              }}
            />
          ) : null}
        </section>
        <div className="graph-divider" />
        <NodeDetail node={selected} />
      </main>
    </div>
  );
}
