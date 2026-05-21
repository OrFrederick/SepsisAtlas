import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResultCard from "../../src/components/ResultCard";
import type { Row } from "../../src/lib/types";

const baseRow: Row = {
  row_id: "r1",
  paper_ref: "Schlapbach 2018",
  cohort_label: "Pediatric ICU",
  file_name: "Schlapbach_2018",
  cohort_size_n: 145,
  population_description: null,
  population_location: null,
  study_design: null,
  mortality_rate_pct: null,
  mortality_timepoint: null,
  predictors: null,
  predictor_canonical: "Lactate",
  outcome: "28-day mortality",
  outcome_type: "mortality",
  outcome_window_days: 28,
  model_specification: null,
  effect_size_str: "OR 2.10",
  effect_type: "OR",
  effect_value: 2.1,
  ci_lo: 1.2,
  ci_hi: 3.4,
  p_value: 0.001,
  auc: null,
  anchor_page: 4,
  anchor_bbox: "10,10,200,200",
  anchor_text: "Lactate >2 mmol/L was associated with…",
  anchor_section: null,
  verifier_verdict: "ok",
  verifier_score: 0.92,
};

afterEach(cleanup);

describe("ResultCard", () => {
  it("renders the verdict badge with the matching CSS class", () => {
    render(<ResultCard row={baseRow} viewerHref="/viewer/X?page=1" />);
    expect(screen.getByText("ok")).toHaveClass("badge", "ok");
  });

  it("maps verifier_verdict=weak to the warn class", () => {
    render(
      <ResultCard
        row={{ ...baseRow, verifier_verdict: "weak" }}
        viewerHref="/viewer/X?page=1"
      />,
    );
    expect(screen.getByText("weak")).toHaveClass("badge", "warn");
  });

  it("renders the CI string only when both ci_lo and ci_hi are present", () => {
    const { rerender } = render(
      <ResultCard row={baseRow} viewerHref="/viewer/X?page=1" />,
    );
    expect(screen.getByText(/95% CI 1.2–3.4/)).toBeInTheDocument();
    rerender(
      <ResultCard
        row={{ ...baseRow, ci_lo: null }}
        viewerHref="/viewer/X?page=1"
      />,
    );
    expect(screen.queryByText(/95% CI/)).not.toBeInTheDocument();
  });

  it("renders the page badge when anchor_page is set", () => {
    render(<ResultCard row={baseRow} viewerHref="/viewer/X?page=4" />);
    expect(screen.getByText("p. 4")).toBeInTheDocument();
  });

  it("omits the page badge when anchor_page is null", () => {
    render(
      <ResultCard
        row={{ ...baseRow, anchor_page: null }}
        viewerHref="/viewer/X?page=1"
      />,
    );
    expect(screen.queryByText(/^p\./)).not.toBeInTheDocument();
  });

  it("fires onSelect on click", async () => {
    const onSelect = vi.fn();
    render(
      <ResultCard
        row={baseRow}
        viewerHref="/viewer/X?page=4"
        onSelect={onSelect}
      />,
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onSelect).toHaveBeenCalledWith(baseRow, "/viewer/X?page=4");
  });

  it("fires onSelect on Enter keypress", async () => {
    const onSelect = vi.fn();
    render(
      <ResultCard
        row={baseRow}
        viewerHref="/viewer/X?page=4"
        onSelect={onSelect}
      />,
    );
    const card = screen.getByRole("button");
    card.focus();
    await userEvent.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("adds the .active class when active=true", () => {
    render(
      <ResultCard
        row={baseRow}
        viewerHref="/viewer/X?page=4"
        active
      />,
    );
    expect(screen.getByRole("button")).toHaveClass("active");
  });
});
