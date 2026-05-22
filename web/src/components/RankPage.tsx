import { useState } from "react";
import RankForm from "./RankForm";
import RankTable from "./RankTable";
import { buildRankUrl } from "../lib/rank";
import type { RankFilters, RankResponse } from "../lib/rank";

type Props = { backendUrl: string };

const INITIAL: RankFilters = {
  outcomeType: "mortality",
  windowDays: "28",
  paperRef: "",
  populationContains: "",
  topK: 50,
};

type State =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "success"; data: RankResponse };

export default function RankPage({ backendUrl }: Props) {
  const [filters, setFilters] = useState<RankFilters>(INITIAL);
  const [state, setState] = useState<State>({ kind: "idle" });

  // Pass the prop through unchanged. The previous `window.location.origin`
  // fallback was a hydration hazard: server render returned "" (no `window`),
  // client render returned the document origin, producing different anchor
  // hrefs on the same row. Empty string is already correct for same-host
  // deploys — the browser resolves `/viewer/...` against the current document.
  const backendOrigin = backendUrl;

  const submit = async () => {
    setState({ kind: "loading" });
    try {
      const resp = await fetch(buildRankUrl(backendUrl, filters));
      if (!resp.ok) {
        setState({ kind: "error", message: `${resp.status} ${resp.statusText || "error"}` });
        return;
      }
      const data = (await resp.json()) as RankResponse;
      setState({ kind: "success", data });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "unknown";
      setState({ kind: "error", message: `Network error: ${msg}` });
    }
  };

  return (
    <>
      <h1 style={{ margin: "0 0 12px", fontSize: 18 }}>Rank predictors</h1>
      <p style={{ color: "var(--fg-muted)", margin: "0 0 16px" }}>
        Pick an outcome and (optionally) a window. Predictors are ranked by
        best-available metric: AUC &gt; c-index &gt; OR &gt; HR &gt; RR.
      </p>
      <RankForm
        value={filters}
        onChange={setFilters}
        onSubmit={submit}
        disabled={state.kind === "loading"}
      />
      {state.kind === "success" && state.data.fallback_note && (
        <div
          style={{
            padding: "8px 12px",
            marginBottom: 8,
            background: "#fff7ed",
            border: "1px solid #fed7aa",
            borderRadius: 4,
            fontSize: 13,
          }}
        >
          {state.data.fallback_note}
        </div>
      )}
      <div style={{ fontSize: 12, color: "var(--fg-muted)", marginBottom: 8 }}>
        {state.kind === "idle" && "Submit to load ranking."}
        {state.kind === "loading" && "Loading…"}
        {state.kind === "error" && state.message}
        {state.kind === "success" && `${state.data.rows.length} predictor(s).`}
      </div>
      {state.kind === "success" && (
        <RankTable rows={state.data.rows} backendOrigin={backendOrigin} />
      )}
    </>
  );
}
