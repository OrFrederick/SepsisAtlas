import { describe, expect, it } from "vitest";
import { viewerHrefFor } from "../../src/lib/rank";
import type { SupportingRow } from "../../src/lib/rank";

function row(overrides: Partial<SupportingRow> = {}): SupportingRow {
  return {
    paper_ref: "P 2024",
    cohort_label: "Adult",
    predictor: "Lactate",
    effect_size_str: "AUC 0.82",
    effect_type: null,
    effect_value: null,
    auc: 0.82,
    c_index: null,
    file_name: "Ren_2022",
    anchor_page: 4,
    anchor_bbox: null,
    anchor_text: null,
    ...overrides,
  };
}

describe("viewerHrefFor", () => {
  it("returns null when file_name is missing", () => {
    expect(viewerHrefFor("https://atlas", row({ file_name: null }))).toBeNull();
  });

  it("emits page and stem with no bbox when anchor_bbox is null", () => {
    expect(viewerHrefFor("https://atlas", row())).toBe(
      "https://atlas/viewer/Ren_2022?page=4",
    );
  });

  it("encodes bbox from a number tuple at 2dp", () => {
    const href = viewerHrefFor(
      "https://atlas",
      row({ anchor_bbox: [10.123, 20.5, 300.001, 400.999] }),
    );
    expect(href).toBe(
      "https://atlas/viewer/Ren_2022?page=4&bbox=10.12,20.50,300.00,401.00&origin=tl",
    );
  });

  it("passes through a comma-string bbox unchanged", () => {
    // Regression: the backend serializes anchor_bbox as a comma-joined
    // string. Previously viewerHrefFor only handled the array branch and
    // silently dropped the bbox highlight on every supporting-row link.
    const href = viewerHrefFor(
      "https://atlas",
      row({ anchor_bbox: "10,20,300,400" }),
    );
    expect(href).toBe(
      "https://atlas/viewer/Ren_2022?page=4&bbox=10,20,300,400&origin=tl",
    );
  });

  it("ignores empty-string bbox", () => {
    expect(viewerHrefFor("https://atlas", row({ anchor_bbox: "" }))).toBe(
      "https://atlas/viewer/Ren_2022?page=4",
    );
  });

  it("ignores array bbox of wrong length", () => {
    expect(
      viewerHrefFor("https://atlas", row({ anchor_bbox: [1, 2, 3] as never })),
    ).toBe("https://atlas/viewer/Ren_2022?page=4");
  });

  it("clamps invalid anchor_page to 1", () => {
    expect(viewerHrefFor("https://atlas", row({ anchor_page: -3 }))).toBe(
      "https://atlas/viewer/Ren_2022?page=1",
    );
  });

  it("encodes stem characters that need it", () => {
    const href = viewerHrefFor("https://atlas", row({ file_name: "Ren&2022" }));
    expect(href).toBe("https://atlas/viewer/Ren%262022?page=4");
  });

  it("works with empty base origin (relative URLs for same-host deploys)", () => {
    expect(viewerHrefFor("", row())).toBe("/viewer/Ren_2022?page=4");
  });
});
