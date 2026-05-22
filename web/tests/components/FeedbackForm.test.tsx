import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FeedbackForm } from "../../src/components/FeedbackForm";

function setup(props: Partial<React.ComponentProps<typeof FeedbackForm>> = {}) {
  return render(<FeedbackForm {...props} />);
}

afterEach(cleanup);

describe("FeedbackForm", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ ok: true, issueUrl: "https://github.com/o/r/issues/9" }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
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

  it("submits valid payload and shows success state with issue link", async () => {
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Site is broken");
    await user.type(screen.getByLabelText(/details|body/i), "Pages return blank.");
    await user.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() => expect(screen.getByText(/thanks/i)).toBeInTheDocument());
    const link = screen.getByRole("link", { name: /view issue|on github/i });
    expect(link).toHaveAttribute("href", "https://github.com/o/r/issues/9");
  });

  it("shows error state on 4xx/5xx and keeps form contents", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(
      JSON.stringify({ ok: false, error: "invalid" }),
      { status: 400, headers: { "content-type": "application/json" } },
    )));
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Title here");
    await user.type(screen.getByLabelText(/details|body/i), "Long enough body text.");
    await user.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() => expect(screen.getByText(/couldn.t submit|error|failed/i)).toBeInTheDocument());
    expect((screen.getByLabelText(/title/i) as HTMLInputElement).value).toBe("Title here");
  });

  it("sends honeypot field as empty string", async () => {
    const fetchMock = vi.fn<typeof fetch>(async () => new Response(
      JSON.stringify({ ok: true, issueUrl: "x" }),
      { status: 200 },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    setup();
    await user.type(screen.getByLabelText(/title/i), "Title here");
    await user.type(screen.getByLabelText(/details|body/i), "Long enough body text.");
    await user.click(screen.getByRole("button", { name: /submit/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit | undefined;
    const body = JSON.parse(String(init?.body));
    expect(body.website).toBe("");
    expect(typeof body.formMountedAtMs).toBe("number");
  });
});
