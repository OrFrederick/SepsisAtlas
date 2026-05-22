import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RankPage from "../../src/components/RankPage";

const fakeResponse = {
  fallback_note: "Used fallback metric for some predictors.",
  rows: [
    {
      predictor_canonical: "Lactate",
      best_metric: "AUC",
      best_value: 0.82,
      best_ci_lo: 0.78,
      best_ci_hi: 0.86,
      n_studies: 4,
      best_paper_ref: "Schlapbach 2018",
      supporting_rows: [
        {
          paper_ref: "Schlapbach 2018",
          cohort_label: "Pediatric ICU",
          predictor: "Lactate",
          effect_size_str: "AUC 0.82",
          auc: 0.82,
          c_index: null,
          effect_type: null,
          effect_value: null,
          file_name: "Schlapbach_2018",
          anchor_page: 4,
          anchor_bbox: [10, 10, 200, 200],
          anchor_text: "Lactate >2 mmol/L",
        },
      ],
    },
  ],
};

type FetchSpy = ReturnType<typeof vi.spyOn<typeof globalThis, "fetch">>;
let fetchSpy: FetchSpy;

beforeEach(() => {
  fetchSpy = vi.spyOn(globalThis, "fetch") as unknown as FetchSpy;
  fetchSpy.mockResolvedValue(
    new Response(JSON.stringify(fakeResponse), { status: 200 }),
  );
});

afterEach(() => {
  fetchSpy.mockRestore();
  cleanup();
});

describe("RankPage", () => {
  it("submits a request to /rank_predictors with form values as a query", async () => {
    render(<RankPage backendUrl="http://api" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    await waitFor(() => expect(fetchSpy).toHaveBeenCalled());
    const url = fetchSpy.mock.calls[0][0] as string;
    expect(url).toContain("http://api/rank_predictors");
    expect(url).toContain("outcome_type=mortality");
    expect(url).toContain("outcome_window_days=28");
  });

  it("renders fallback_note as a banner", async () => {
    render(<RankPage backendUrl="" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    expect(
      await screen.findByText(/Used fallback metric/i),
    ).toBeInTheDocument();
  });

  it("renders a predictor row from the response", async () => {
    render(<RankPage backendUrl="" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    expect(await screen.findByText("Lactate")).toBeInTheDocument();
    expect(screen.getByText("Schlapbach 2018")).toBeInTheDocument();
  });

  it("toggles the supporting-rows drawer", async () => {
    render(<RankPage backendUrl="" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    await screen.findByText("Lactate");
    const details = screen.getByRole("button", { name: /Details/i });
    expect(screen.queryByText(/Lactate >2 mmol\/L/)).not.toBeInTheDocument();
    await userEvent.click(details);
    expect(screen.getByText(/Lactate >2 mmol\/L/)).toBeInTheDocument();
    await userEvent.click(details);
    expect(screen.queryByText(/Lactate >2 mmol\/L/)).not.toBeInTheDocument();
  });

  it("disables the submit button while loading", async () => {
    let resolveFetch!: (r: Response) => void;
    fetchSpy.mockImplementation(
      () => new Promise<Response>((r) => (resolveFetch = r)),
    );
    render(<RankPage backendUrl="" />);
    const btn = screen.getByRole("button", { name: /Rank/i });
    await userEvent.click(btn);
    expect(btn).toBeDisabled();
    resolveFetch(new Response(JSON.stringify({ rows: [] }), { status: 200 }));
    await waitFor(() => expect(btn).not.toBeDisabled());
  });

  it("surfaces a fetch error", async () => {
    fetchSpy.mockResolvedValueOnce(
      new Response("boom", { status: 500, statusText: "Server Error" }),
    );
    render(<RankPage backendUrl="" />);
    await userEvent.click(screen.getByRole("button", { name: /Rank/i }));
    expect(await screen.findByText(/Server Error|500/i)).toBeInTheDocument();
  });
});
