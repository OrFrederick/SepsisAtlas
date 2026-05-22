import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FeedbackForm } from "../../src/components/FeedbackForm";

function setup(props: Partial<React.ComponentProps<typeof FeedbackForm>> = {}) {
  return render(<FeedbackForm {...props} />);
}

const MOUNT_RESPONSE = {
  ok: true,
  mount: { ts: 1_700_000_000_000, sig: "a".repeat(64) },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

// Routes mount-token GETs to a fixed token and POSTs to `postBody`. Tests
// that need richer behaviour stub fetch themselves.
function stubFetch(postResponse: Response): ReturnType<typeof vi.fn> {
  const fn = vi.fn<typeof fetch>(async (input) => {
    const url = typeof input === "string" ? input : (input as Request).url ?? String(input);
    if (url.includes("/api/feedback/mount")) return jsonResponse(MOUNT_RESPONSE);
    return postResponse.clone();
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(cleanup);

describe("FeedbackForm", () => {
  beforeEach(() => {
    stubFetch(jsonResponse({ ok: true, issueUrl: "https://github.com/o/r/issues/9" }));
  });

  it("renders type select, title, body, optional email", () => {
    setup();
    expect(screen.getByLabelText(/type/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/title/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/details|body/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument();
  });

  it("prefills from initialType and initialPaper props", () => {
    setup({ initialType: "wrong-data", initialPaper: "Seymour_2016" });
    expect((screen.getByLabelText(/type/i) as HTMLSelectElement).value).toBe("wrong-data");
    expect(screen.getByText(/Seymour_2016/)).toBeInTheDocument();
  });

  it("submits valid payload and shows success state without leaking GitHub URL", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Site is broken");
    await user.type(screen.getByLabelText(/details|body/i), "Pages return blank.");
    await user.click(screen.getByRole("button", { name: /send feedback/i }));
    await waitFor(() => expect(screen.getByText(/thanks/i)).toBeInTheDocument());
    expect(screen.queryByRole("link", { name: /view issue|github/i })).toBeNull();
    expect(screen.queryByText(/github/i)).toBeNull();
  });

  it("shows error state on 4xx/5xx and keeps form contents", async () => {
    stubFetch(jsonResponse({ ok: false, error: "invalid" }, 400));
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Title here");
    await user.type(screen.getByLabelText(/details|body/i), "Long enough body text.");
    await user.click(screen.getByRole("button", { name: /send feedback/i }));
    await waitFor(() => expect(screen.getByText(/couldn.t submit|error|failed/i)).toBeInTheDocument());
    expect((screen.getByLabelText(/title/i) as HTMLInputElement).value).toBe("Title here");
  });

  it("sends honeypot empty string and the server-issued mount token in the POST body", async () => {
    const fetchMock = stubFetch(jsonResponse({ ok: true, issueUrl: "x" }));
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Title here");
    await user.type(screen.getByLabelText(/details|body/i), "Long enough body text.");
    await user.click(screen.getByRole("button", { name: /send feedback/i }));
    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter((c) => {
        const url = typeof c[0] === "string" ? c[0] : (c[0] as Request).url;
        return url === "/api/feedback";
      });
      expect(postCalls.length).toBeGreaterThan(0);
    });
    const postCall = fetchMock.mock.calls.find((c) => {
      const url = typeof c[0] === "string" ? c[0] : (c[0] as Request).url;
      return url === "/api/feedback";
    });
    const init = postCall?.[1] as RequestInit | undefined;
    const body = JSON.parse(String(init?.body));
    expect(body.website).toBe("");
    expect(body.mount).toEqual(MOUNT_RESPONSE.mount);
  });
});
