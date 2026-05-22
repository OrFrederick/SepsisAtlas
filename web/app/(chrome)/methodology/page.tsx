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
    module: "src/parse/ · Docling",
    role: "PDF → sections, tables, tokens. Char→bbox offsets recorded so any extracted span resolves back to a page coordinate.",
    kind: "deterministic",
  },
  {
    n: "2",
    name: "Extract",
    module: "src/extract/ · Sonnet 4.5",
    role: "Two schema-guided passes. First cohort enumeration, then predictor extraction per cohort. Model writes the verbatim effect_size_str. Numbers are never computed by the model.",
    kind: "llm",
  },
  {
    n: "2b",
    name: "Resolve anchor",
    module: "src/extract/anchor_resolver.py",
    role: "LLM emits anchor_text + section. Substring search over the parsed JSON recovers (page, bbox). Coordinates cannot be hallucinated.",
    kind: "deterministic",
  },
  {
    n: "3",
    name: "Verify",
    module: "src/extract/verify_nli.py",
    role: "Regex matches numeric atoms (AUC, CI, p, sens/spec) against the anchor span. DeBERTa-MNLI checks free-text claims. Local inference, no API call.",
    kind: "llm",
  },
  {
    n: "4",
    name: "Query",
    module: "src/api/ · Haiku intent + SQL",
    role: "Natural language → structured intent → deterministic SQL. Answerability gate refuses queries too vague to narrow the corpus. Rows come from the DB, not the model.",
    kind: "hybrid",
  },
  {
    n: "5",
    name: "Render",
    module: "web/ + FastAPI viewer",
    role: "Chat shell shows ranked rows with verifier badges. Click any cell → PDF.js opens the source page with a yellow rectangle drawn over the exact bbox.",
    kind: "deterministic",
  },
];

const INVARIANTS = [
  "anchor_text must be a verbatim substring of the parsed PDF, or the row is rejected.",
  "Numbers come from the SQLite row, not the model. effect_size_str is verbatim. Numeric fields are regex-derived.",
  "Stage 3 runs locally. No API call, no network.",
  "Every model call is logged append-only with stage, prompt hash, tokens, cost, latency.",
  "Held-out set (Gai 2022, Seymour 2016, Wang 2023, Zhang 2021) is never used for prompt tuning.",
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
        Every row in the chat started as a sentence in a PDF. A fixed pipeline
        gets it from one to the other. Deterministic code handles parsing,
        anchoring, storage, and rendering. LLMs only do the judgment work, in
        narrow stages, with their output reconciled against the source.
      </p>

      <h2 className="method__h2" id="pipeline">Pipeline</h2>
      <p className="method__p">
        Stages run in order. Each stage&apos;s output is persisted before the
        next stage starts. No in-memory hand-off between an LLM and the next
        consumer.
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
      <ul className="method__invariants">
        {INVARIANTS.map((line) => (
          <li className="method__invariant" key={line}>
            {line}
          </li>
        ))}
      </ul>

      <p className="method__foot">
        Full walkthrough with diagrams lives in <code>docs/pipeline.md</code> in
        the source repository. Stage changes land in the same PR as that
        document.
      </p>
    </article>
  );
}
