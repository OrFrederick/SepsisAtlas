export const metadata = {
  title: "Sepsis Atlas — Methodology",
  description: "How Sepsis Atlas turns PDFs into anchored, queryable evidence.",
};

type Stage = {
  n: string;
  name: string;
  module: string;
  role: string;
  kind: "deterministic" | "llm" | "hybrid";
};

const STAGES: Stage[] = [
  {
    n: "1",
    name: "Parse",
    module: "src/parse/ — Docling",
    role: "Each PDF is segmented into sections, tables, and tokens. Char→bbox offsets are recorded so any extracted span can be located on the page.",
    kind: "deterministic",
  },
  {
    n: "2",
    name: "Extract",
    module: "src/extract/ — Sonnet 4.5",
    role: "Two schema-guided LLM passes: cohort enumeration, then predictor extraction per cohort. The model writes the verbatim effect_size_str; it never computes numbers.",
    kind: "llm",
  },
  {
    n: "2b",
    name: "Resolve anchor",
    module: "src/extract/anchor_resolver.py",
    role: "The LLM emits only anchor_text + section. A deterministic substring search over the parsed JSON recovers (page, bbox). No model can fabricate a coordinate.",
    kind: "deterministic",
  },
  {
    n: "3",
    name: "Verify",
    module: "src/extract/verify_nli.py",
    role: "Local hybrid verifier. Regex checks numeric atoms (AUC, CI, p, sens/spec) against the anchor span. DeBERTa-MNLI checks free-text claims. No LLM, no network.",
    kind: "hybrid",
  },
  {
    n: "4",
    name: "Store",
    module: "src/sepsis_atlas/db.py — SQLite",
    role: "papers → study_cohort → predictor_model. Every LLM call is logged append-only with cost, latency, tokens, prompt hash. Verdicts and scores live next to the row.",
    kind: "deterministic",
  },
  {
    n: "5",
    name: "Query",
    module: "src/api/ — Haiku intent + SQL",
    role: "Natural language → structured intent → deterministic SQL. An answerability gate refuses queries too vague to narrow the corpus. Numbers come from rows, not the LLM.",
    kind: "hybrid",
  },
  {
    n: "6",
    name: "Meta-analysis",
    module: "src/stats/ — DerSimonian-Laird",
    role: "Harmonize effect sizes, random-effects pool, emit forest plot PNG with τ², I², per-study weights. Validated to 0.009% of a hand calculation.",
    kind: "deterministic",
  },
  {
    n: "7",
    name: "Render",
    module: "web/ + FastAPI viewer",
    role: "Chat shell shows ranked rows with verifier badges. Click any cell → PDF.js opens the source page with a yellow rectangle drawn over the exact bbox.",
    kind: "deterministic",
  },
];

const INVARIANTS = [
  {
    title: "Anchor contract",
    body: "Every extracted row carries (anchor_page, anchor_bbox, anchor_text, anchor_section). The anchor_text must be a verbatim substring of the parsed PDF or the row is rejected.",
  },
  {
    title: "Numbers from the database",
    body: "The LLM never computes a value. effect_size_str is preserved verbatim; numeric fields are derived from it by regex. The chat answers cite DB rows, not model output.",
  },
  {
    title: "Verifier is local",
    body: "Stage 3 has no API call. A row is kept, badged ✓, or badged ~ purely from regex + NLI scores against the anchor span — reproducible and free.",
  },
  {
    title: "Append-only audit",
    body: "llm_calls logs every model hit (stage, prompt hash, tokens, cost, latency). Runs are diffable; cost is exposed read-only at /health/cost.",
  },
  {
    title: "Held-out validation",
    body: "Gai 2022, Seymour 2016, Wang 2023, Zhang 2021 are the ground-truth set. Prompts are never tuned on these papers. Scores are reported as cohort recall, exact match, and within-1% numeric tolerance.",
  },
];

const KIND_LABEL: Record<Stage["kind"], string> = {
  deterministic: "deterministic",
  llm: "LLM",
  hybrid: "hybrid",
};

export default function MethodologyPage() {
  return (
    <article className="method">
      <h1 className="method__h1">How Sepsis Atlas turns PDFs into evidence</h1>
      <p className="method__lede">
        Every row you see in the chat started as a sentence in a PDF. A fixed
        pipeline gets it from one to the other: deterministic code handles
        parsing, anchoring, verification, storage, and rendering; LLMs only
        do the judgment work, in narrow stages, with their output reconciled
        against the source.
      </p>

      <dl className="method__stats">
        <div className="method__stat">
          <dt>Pipeline stages</dt>
          <dd>{STAGES.length}</dd>
        </div>
        <div className="method__stat">
          <dt>LLM stages</dt>
          <dd>{STAGES.filter((s) => s.kind === "llm").length}</dd>
        </div>
        <div className="method__stat">
          <dt>Deterministic stages</dt>
          <dd>{STAGES.filter((s) => s.kind === "deterministic").length}</dd>
        </div>
        <div className="method__stat">
          <dt>Held-out papers</dt>
          <dd>4</dd>
        </div>
      </dl>

      <h2 className="method__h2" id="pipeline">Pipeline</h2>
      <p className="method__p">
        Stages run in order. Each stage&apos;s output is persisted before the
        next stage starts — there is no in-memory hand-off between an LLM and
        the next consumer.
      </p>
      <ol className="method__stages">
        {STAGES.map((s) => (
          <li className="method__stage" data-kind={s.kind} key={s.n}>
            <div className="method__stage-n">{s.n}</div>
            <div className="method__stage-body">
              <div className="method__stage-head">
                <span className="method__stage-name">{s.name}</span>
                <span className="method__stage-kind" data-kind={s.kind}>
                  {KIND_LABEL[s.kind]}
                </span>
              </div>
              <div className="method__stage-module">{s.module}</div>
              <p className="method__stage-role">{s.role}</p>
            </div>
          </li>
        ))}
      </ol>

      <h2 className="method__h2" id="invariants">What keeps it honest</h2>
      <p className="method__p">
        Five rules are enforced by code, not by trust. Any row or response
        that violates one is rejected or rewritten — never silently passed
        through.
      </p>
      <ul className="method__invariants">
        {INVARIANTS.map((it) => (
          <li className="method__invariant" key={it.title}>
            <div className="method__invariant-title">{it.title}</div>
            <p className="method__invariant-body">{it.body}</p>
          </li>
        ))}
      </ul>

      <h2 className="method__h2" id="audit">Per-row audit trail</h2>
      <p className="method__p">
        Each predictor row links to the cohort it belongs to, the verifier
        verdict and score, and every LLM call that produced it — model,
        prompt hash, tokens, cost, latency. Click the anchor to open the
        exact PDF page with the bounding box drawn.
      </p>

      <p className="method__foot">
        The authoritative walkthrough — including Mermaid diagrams of each
        stage — lives in{" "}
        <code>docs/pipeline.md</code> in the source repository. If a stage
        changes, that document changes in the same PR.
      </p>
    </article>
  );
}
