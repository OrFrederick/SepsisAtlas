import { afterEach } from "vitest";
import { describe, it, expect } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { FeedbackButton } from "../../src/components/FeedbackButton";

afterEach(() => cleanup());

describe("FeedbackButton", () => {
  it("renders a link to /feedback with type query when only type set", () => {
    render(<FeedbackButton type="idea" label="Idea" />);
    const a = screen.getByRole("link", { name: /idea/i });
    expect(a.getAttribute("href")).toBe("/feedback?type=idea");
  });

  it("includes paper stem when provided", () => {
    render(<FeedbackButton type="wrong-data" paper="Seymour_2016" label="Report" />);
    const a = screen.getByRole("link", { name: /report/i });
    expect(a.getAttribute("href")).toBe("/feedback?type=wrong-data&paper=Seymour_2016");
  });

  it("base64url-encodes rowContext", () => {
    render(<FeedbackButton type="wrong-data" paper="X" rowContext={{ a: 1 }} label="r" />);
    const href = screen.getByRole("link").getAttribute("href")!;
    const m = href.match(/row=([^&]+)/)!;
    const decoded = JSON.parse(Buffer.from(m[1], "base64url").toString("utf8"));
    expect(decoded).toEqual({ a: 1 });
  });
});
