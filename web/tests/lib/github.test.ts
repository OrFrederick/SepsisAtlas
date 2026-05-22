import { describe, it, expect, vi, beforeEach } from "vitest";
import { createFeedbackIssue } from "../../src/lib/github";
import type { FeedbackPayload } from "../../src/lib/feedback-schema";

const base: FeedbackPayload = {
  type: "wrong-data",
  title: "Age mean wrong in Table 2",
  body: "Reported 65, paper says 67.",
  paperStem: "Seymour_2016",
  website: "",
};

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("createFeedbackIssue", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/issues")) {
        return jsonResponse(201, { html_url: "https://github.com/o/r/issues/123" });
      }
      if (url.endsWith("/labels")) {
        return jsonResponse(201, {});
      }
      return jsonResponse(404, {});
    });
  });

  it("POSTs to /repos/{owner}/{repo}/issues with the right shape", async () => {
    const res = await createFeedbackIssue(base, {
      fetch: fetchMock,
      repo: "OrFrederick/SepsisAtlas",
      token: "tok",
    });
    expect(res.issueUrl).toBe("https://github.com/o/r/issues/123");
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/issues"))!;
    const init = call[1] as RequestInit;
    expect(call[0]).toBe("https://api.github.com/repos/OrFrederick/SepsisAtlas/issues");
    expect((init.headers as Record<string,string>)["Authorization"]).toBe("Bearer tok");
    const payload = JSON.parse(String(init.body));
    expect(payload.title).toBe("[feedback:wrong-data] Age mean wrong in Table 2");
    expect(payload.labels).toEqual(expect.arrayContaining([
      "feedback", "from-website", "feedback:wrong-data", "needs-triage", "paper:Seymour_2016",
    ]));
    expect(payload.body).toContain("**Type:** wrong-data");
    expect(payload.body).toContain("**Paper:** Seymour_2016");
    expect(payload.body).toContain("Reported 65, paper says 67.");
  });

  it("omits paper:* label when no paperStem given", async () => {
    await createFeedbackIssue({ ...base, paperStem: undefined, type: "idea" }, {
      fetch: fetchMock,
      repo: "o/r",
      token: "t",
    });
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/issues"))!;
    const payload = JSON.parse(String((call[1] as RequestInit).body));
    expect(payload.labels.some((l: string) => l.startsWith("paper:"))).toBe(false);
  });

  it("creates the paper:<stem> label first when paperStem present", async () => {
    await createFeedbackIssue(base, { fetch: fetchMock, repo: "o/r", token: "t" });
    const labelCall = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/labels"));
    expect(labelCall).toBeDefined();
    const body = JSON.parse(String((labelCall![1] as RequestInit).body));
    expect(body.name).toBe("paper:Seymour_2016");
  });

  it("treats 422 'already_exists' on label-create as success", async () => {
    fetchMock = vi.fn(async (url: string) => {
      if (url.endsWith("/labels")) {
        return jsonResponse(422, { message: "Validation Failed", errors: [{ code: "already_exists" }] });
      }
      return jsonResponse(201, { html_url: "https://github.com/o/r/issues/1" });
    });
    const res = await createFeedbackIssue(base, { fetch: fetchMock, repo: "o/r", token: "t" });
    expect(res.issueUrl).toBe("https://github.com/o/r/issues/1");
  });

  it("throws if GitHub issues endpoint returns non-2xx", async () => {
    fetchMock = vi.fn(async () => jsonResponse(500, { message: "boom" }));
    await expect(
      createFeedbackIssue({ ...base, paperStem: undefined }, {
        fetch: fetchMock, repo: "o/r", token: "t",
      }),
    ).rejects.toThrow(/500/);
  });

  it("includes contact + rowContext + submitted timestamp in body when provided", async () => {
    await createFeedbackIssue({
      ...base,
      contact: "user@example.com",
      rowContext: { age: 65, outcome: "death" },
    }, { fetch: fetchMock, repo: "o/r", token: "t" });
    const call = fetchMock.mock.calls.find((c) => String(c[0]).endsWith("/issues"))!;
    const payload = JSON.parse(String((call[1] as RequestInit).body));
    expect(payload.body).toContain("user@example.com");
    expect(payload.body).toContain('"age": 65');
    expect(payload.body).toMatch(/\*\*Submitted:\*\* \d{4}-\d{2}-\d{2}T/);
  });
});
