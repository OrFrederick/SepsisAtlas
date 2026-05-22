import { describe, it, expect } from "vitest";
import { validateFeedback, FEEDBACK_TYPES } from "../../src/lib/feedback-schema";

const base = {
  type: "bug",
  title: "Form blank on Safari",
  body: "When I open /papers on Safari 17, the table is empty.",
  website: "",
  formMountedAtMs: 1_700_000_000_000,
};

describe("validateFeedback", () => {
  it("accepts a minimal valid payload", () => {
    const r = validateFeedback(base);
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.type).toBe("bug");
  });

  it.each(FEEDBACK_TYPES)("accepts type %s", (t) => {
    expect(validateFeedback({ ...base, type: t }).ok).toBe(true);
  });

  it("rejects unknown type", () => {
    expect(validateFeedback({ ...base, type: "spam" }).ok).toBe(false);
  });

  it("rejects title shorter than 5 chars", () => {
    expect(validateFeedback({ ...base, title: "hi" }).ok).toBe(false);
  });

  it("rejects title longer than 120 chars", () => {
    expect(validateFeedback({ ...base, title: "x".repeat(121) }).ok).toBe(false);
  });

  it("rejects title that is only whitespace-padded to meet minimum", () => {
    expect(validateFeedback({ ...base, title: "   hi   " }).ok).toBe(false);
  });

  it("rejects body shorter than 10 chars", () => {
    expect(validateFeedback({ ...base, body: "short" }).ok).toBe(false);
  });

  it("rejects body longer than 5000 chars", () => {
    expect(validateFeedback({ ...base, body: "x".repeat(5001) }).ok).toBe(false);
  });

  it("rejects when honeypot field is non-empty", () => {
    const r = validateFeedback({ ...base, website: "http://spam.example" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.error).toBe("honeypot");
  });

  it("rejects invalid email in contact", () => {
    expect(validateFeedback({ ...base, contact: "not-an-email" }).ok).toBe(false);
  });

  it("accepts a valid email in contact", () => {
    const r = validateFeedback({ ...base, contact: "user@example.com" });
    expect(r.ok).toBe(true);
  });

  it("accepts an optional paperStem matching [A-Za-z0-9_-]+", () => {
    expect(validateFeedback({ ...base, paperStem: "Seymour_2016" }).ok).toBe(true);
  });

  it("rejects a paperStem with path traversal characters", () => {
    expect(validateFeedback({ ...base, paperStem: "../etc/passwd" }).ok).toBe(false);
  });

  it("accepts rowContext as arbitrary JSON-serializable value", () => {
    const r = validateFeedback({ ...base, rowContext: { age: 65, outcome: "death" } });
    expect(r.ok).toBe(true);
  });

  it("rejects rowContext that is not plain JSON-serializable", () => {
    const cyclic: any = {}; cyclic.self = cyclic;
    expect(validateFeedback({ ...base, rowContext: cyclic }).ok).toBe(false);
  });

  it("rejects non-object input", () => {
    expect(validateFeedback("nope").ok).toBe(false);
    expect(validateFeedback(null).ok).toBe(false);
  });

  it("rejects when formMountedAtMs is missing (anti-bot guard cannot be opt-out)", () => {
    const { formMountedAtMs: _drop, ...rest } = base;
    expect(validateFeedback(rest).ok).toBe(false);
  });

  it("rejects when formMountedAtMs is not a finite number", () => {
    expect(validateFeedback({ ...base, formMountedAtMs: "1700000000000" }).ok).toBe(false);
    expect(validateFeedback({ ...base, formMountedAtMs: Number.NaN }).ok).toBe(false);
    expect(validateFeedback({ ...base, formMountedAtMs: Number.POSITIVE_INFINITY }).ok).toBe(false);
  });

  it("strips control characters from the title before length-checking", () => {
    const dirty = "Hello" + "\n\r\t" + "world!";
    const r = validateFeedback({ ...base, title: dirty });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.title).toBe("Helloworld!");
  });

  it("rejects a paperStem longer than 64 chars (label-namespace + GH label cap)", () => {
    expect(validateFeedback({ ...base, paperStem: "a".repeat(65) }).ok).toBe(false);
    expect(validateFeedback({ ...base, paperStem: "a".repeat(64) }).ok).toBe(true);
  });
});
