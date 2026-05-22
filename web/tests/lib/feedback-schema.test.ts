import { describe, it, expect } from "vitest";
import { validateFeedback, FEEDBACK_TYPES } from "../../src/lib/feedback-schema";

const base = {
  type: "bug",
  title: "Form blank on Safari",
  body: "When I open /papers on Safari 17, the table is empty.",
  website: "",
  mount: { ts: 1_700_000_000_000, sig: "a".repeat(64) },
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

  it("rejects when mount token is missing (anti-bot guard cannot be opt-out)", () => {
    const { mount: _drop, ...rest } = base;
    expect(validateFeedback(rest).ok).toBe(false);
  });

  it("rejects mount token with bad shape", () => {
    expect(validateFeedback({ ...base, mount: "string" }).ok).toBe(false);
    expect(validateFeedback({ ...base, mount: { ts: "1", sig: "a".repeat(64) } }).ok).toBe(false);
    expect(validateFeedback({ ...base, mount: { ts: Number.NaN, sig: "a".repeat(64) } }).ok).toBe(false);
    expect(validateFeedback({ ...base, mount: { ts: 1, sig: "not-hex" } }).ok).toBe(false);
    expect(validateFeedback({ ...base, mount: { ts: 1, sig: "abc" } }).ok).toBe(false);
  });

  it("strips control characters from the title before length-checking", () => {
    const dirty = "Hello" + "\n\r\t" + "world!";
    const r = validateFeedback({ ...base, title: dirty });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.title).toBe("Helloworld!");
  });

  it("strips U+2028 / U+2029 line/paragraph separators from the title", () => {
    const dirty = "Hello\u2028world\u2029!";
    const r = validateFeedback({ ...base, title: dirty });
    expect(r.ok).toBe(true);
    if (r.ok) expect(r.value.title).toBe("Helloworld!");
  });

  it("rejects a paperStem longer than 40 chars (GitHub label name cap, with `paper:` prefix)", () => {
    expect(validateFeedback({ ...base, paperStem: "a".repeat(41) }).ok).toBe(false);
    expect(validateFeedback({ ...base, paperStem: "a".repeat(40) }).ok).toBe(true);
  });
});
