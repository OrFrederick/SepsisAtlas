import { describe, expect, it } from "vitest";
import {
  isActiveHumanReview,
  verdictKind,
  type HumanReview,
} from "../../src/lib/humanReview";

function review(partial: Partial<HumanReview>): HumanReview {
  return { verdict: "approve", ...partial };
}

describe("isActiveHumanReview", () => {
  it("treats null/undefined as inactive", () => {
    expect(isActiveHumanReview(null)).toBe(false);
    expect(isActiveHumanReview(undefined)).toBe(false);
  });

  it("treats the reserved 'cleared' verdict as inactive (tombstone)", () => {
    expect(isActiveHumanReview(review({ verdict: "cleared" }))).toBe(false);
  });

  it("keeps approve/reject/flag verdicts active", () => {
    expect(isActiveHumanReview(review({ verdict: "approve" }))).toBe(true);
    expect(isActiveHumanReview(review({ verdict: "reject" }))).toBe(true);
    expect(isActiveHumanReview(review({ verdict: "flag" }))).toBe(true);
  });

  it("does NOT hide a real review whose rationale text is 'cleared'", () => {
    // Regression guard for the old rationale-string sentinel: a reviewer who
    // genuinely types "cleared"/"CLEARED" as their rationale must stay visible.
    expect(
      isActiveHumanReview(review({ verdict: "flag", rationale: "cleared" })),
    ).toBe(true);
    expect(
      isActiveHumanReview(review({ verdict: "reject", rationale: "CLEARED" })),
    ).toBe(true);
  });
});

describe("verdictKind", () => {
  it("buckets the verifier and human vocabularies into four kinds", () => {
    expect(verdictKind("pass").cls).toBe("ok");
    expect(verdictKind("ok").cls).toBe("ok");
    expect(verdictKind("approve").cls).toBe("ok");
    expect(verdictKind("weak").cls).toBe("warn");
    expect(verdictKind("flag").cls).toBe("warn");
    expect(verdictKind("fail").cls).toBe("fail");
    expect(verdictKind("reject").cls).toBe("fail");
  });

  it("is case-insensitive and falls back to 'unk' for unknowns", () => {
    expect(verdictKind("APPROVE").cls).toBe("ok");
    expect(verdictKind("mystery").cls).toBe("unk");
    expect(verdictKind(null).cls).toBe("unk");
    expect(verdictKind(undefined).cls).toBe("unk");
  });
});
