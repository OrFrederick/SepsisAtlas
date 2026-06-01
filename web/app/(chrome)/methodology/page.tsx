export const metadata = {
  title: "Sepsis Atlas — Methodology",
  description: "How Sepsis Atlas turns PDFs into anchored, queryable evidence.",
};

type Stage = {
  n: string;
  name: string;
  module: string;
  role: string;
  kind: "deterministic" | "llm" | "local-llm" | "hybrid";
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
    module: "src/extract/ · Claude via OpenRouter",
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
    kind: "local-llm",
  },
  {
    n: "4",
    name: "Query",
    module: "src/api/ · Claude intent + SQL",
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
  "local-llm": "local LLM",
  hybrid: "hybrid",
};

const KIND_BADGE: Record<Stage["kind"], string> = {
  deterministic:
    "text-fg-muted border-border bg-panel-2",
  llm: "text-accent border-accent-soft bg-accent-soft",
  "local-llm": "text-ok border-ok-border bg-ok-soft",
  hybrid: "text-warn border-warn-border bg-warn-soft",
};

export default function MethodologyPage() {
  return (
    <main className="max-w-[760px] mx-auto px-7 pt-[22px] pb-[60px]">
      <h1 className="font-serif font-medium text-[28px] leading-[1.2] m-0 mb-3 text-fg">
        How Sepsis Atlas turns PDFs into evidence
      </h1>
      <p className="text-[15px] leading-[1.6] text-fg-soft m-0 mb-[22px]">
        Every row in the chat started as a sentence in a PDF. A fixed pipeline
        gets it from one to the other. Deterministic code handles parsing,
        anchoring, storage, and rendering. LLMs only do the judgment work, in
        narrow stages, with their output reconciled against the source.
      </p>

      <h2
        id="pipeline"
        className="font-serif font-medium text-xl mt-8 mb-[10px] pt-4 border-t border-border text-fg"
      >
        Pipeline
      </h2>
      <p className="m-0 mb-4 text-fg-soft">
        Stages run in order. Each stage&apos;s output is persisted before the
        next stage starts. No in-memory hand-off between an LLM and the next
        consumer.
      </p>
      <ol className="list-none p-0 m-0 flex flex-col gap-2">
        {STAGES.map((s) => (
          <li
            key={s.n}
            className="grid grid-cols-[36px_1fr] gap-3 items-start bg-panel border border-border rounded-lg py-3 px-[14px]"
          >
            <div className="font-serif text-lg text-fg-muted text-right pr-1">{s.n}</div>
            <div>
              <div className="flex items-baseline gap-[10px] mb-[2px]">
                <span className="font-serif text-base font-medium text-fg">{s.name}</span>
                <span
                  className={`text-[10px] uppercase tracking-[0.5px] py-px px-[6px] rounded-full border ${KIND_BADGE[s.kind]}`}
                >
                  {KIND_LABEL[s.kind]}
                </span>
              </div>
              <div className="font-mono text-xs text-fg-muted mb-[6px]">{s.module}</div>
              <p className="m-0 text-sm leading-[1.55] text-fg-soft">{s.role}</p>
            </div>
          </li>
        ))}
      </ol>

      <h2
        id="invariants"
        className="font-serif font-medium text-xl mt-8 mb-[10px] pt-4 border-t border-border text-fg"
      >
        What keeps it honest
      </h2>
      <ul className="list-none p-0 m-0 flex flex-col gap-1">
        {INVARIANTS.map((line) => (
          <li
            key={line}
            className="relative py-[6px] pl-[22px] text-sm leading-[1.55] text-fg-soft border-t border-border first:border-t-0 before:content-[''] before:absolute before:left-1 before:top-[14px] before:w-[6px] before:h-[6px] before:rounded-full before:bg-accent"
          >
            {line}
          </li>
        ))}
      </ul>

      <p className="mt-6 text-[13px] text-fg-muted">
        Source repository:{" "}
        <code className="font-mono text-xs bg-panel-2 py-px px-[5px] rounded">
          docs/pipeline.md
        </code>{" "}
        walks every stage (including storage, audit logging, and the validation
        harness) with diagrams and code pointers.
      </p>
    </main>
  );
}
