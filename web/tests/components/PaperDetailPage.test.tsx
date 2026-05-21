import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PaperDetailPage from "../../src/components/PaperDetailPage";
import type { Paper, Row } from "../../src/lib/types";

const paper: Paper = {
  file_name: "Ren_2022",
  title: "Test paper",
  year: 2022,
  journal: "JAMA",
  parsed: true,
  translated: false,
  n_rows: 2,
  verdicts: { ok: 1, weak: 1, fail: 0, unverified: 0 },
  last_update: "2024-05-01",
};

const baseRow: Omit<Row, "row_id" | "anchor_page" | "predictor_canonical"> = {
  paper_ref: "Ren 2022",
  cohort_label: null,
  file_name: "Ren_2022",
  cohort_size_n: 100,
  population_description: null,
  population_location: null,
  study_design: null,
  mortality_rate_pct: null,
  mortality_timepoint: null,
  predictors: null,
  outcome: "28-day mortality",
  outcome_type: "mortality",
  outcome_window_days: 28,
  model_specification: null,
  effect_size_str: "OR 2.0",
  effect_type: "OR",
  effect_value: 2,
  ci_lo: null,
  ci_hi: null,
  p_value: null,
  auc: null,
  anchor_bbox: null,
  anchor_text: null,
  anchor_section: null,
  verifier_verdict: "ok",
  verifier_score: 0.9,
};

const rows: Row[] = [
  { ...baseRow, row_id: "r1", anchor_page: 4, predictor_canonical: "Lactate" },
  { ...baseRow, row_id: "r2", anchor_page: 6, predictor_canonical: "Procalcitonin" },
];

afterEach(cleanup);

describe("PaperDetailPage", () => {
  it("renders one card per row", () => {
    render(
      <PaperDetailPage
        paper={paper}
        rows={rows}
        basePath="/"
        defaultViewerUrl="/viewer/Ren_2022?page=4"
      />,
    );
    expect(screen.getAllByRole("button").filter((b) => b.className.includes("card"))).toHaveLength(
      2,
    );
  });

  it("shows the empty-rows message when rows is empty", () => {
    render(
      <PaperDetailPage
        paper={paper}
        rows={[]}
        basePath="/"
        defaultViewerUrl="/viewer/Ren_2022?page=1"
      />,
    );
    expect(screen.getByText(/No extracted rows/)).toBeInTheDocument();
  });

  it("clicking a card marks it active", async () => {
    render(
      <PaperDetailPage
        paper={paper}
        rows={rows}
        basePath="/"
        defaultViewerUrl="/viewer/Ren_2022?page=4"
      />,
    );
    const cards = screen.getAllByRole("button").filter((b) => b.className.includes("card"));
    await userEvent.click(cards[1]);
    expect(cards[1]).toHaveClass("active");
    expect(cards[0]).not.toHaveClass("active");
  });
});
