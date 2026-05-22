import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import PdfViewerPane from "../../src/components/PdfViewerPane";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

describe("PdfViewerPane", () => {
  it("renders the empty hint when src is null", () => {
    render(<PdfViewerPane src={null} emptyHint="click a row" />);
    expect(screen.getByText("click a row")).toBeInTheDocument();
    expect(screen.queryByTitle("PDF viewer")).not.toBeInTheDocument();
  });

  it("mounts an iframe pointing at src when src is non-null", () => {
    render(<PdfViewerPane src="http://localhost/viewer/Ren_2022?page=1" />);
    const iframe = screen.getByTitle("PDF viewer") as HTMLIFrameElement;
    expect(iframe.src).toContain("/viewer/Ren_2022");
  });

  it("calls postMessage instead of swapping src when the new src has the same stem", () => {
    const { rerender } = render(
      <PdfViewerPane src="http://localhost/viewer/Ren_2022?page=1" />,
    );
    const iframe = screen.getByTitle("PDF viewer") as HTMLIFrameElement;
    const originalSrc = iframe.src;
    const postSpy = vi.fn();
    Object.defineProperty(iframe, "contentWindow", {
      configurable: true,
      get: () => ({ postMessage: postSpy }),
    });

    rerender(
      <PdfViewerPane src="http://localhost/viewer/Ren_2022?page=6&bbox=1,2,3,4&origin=tl" />,
    );

    expect(iframe.src).toBe(originalSrc);
    expect(postSpy).toHaveBeenCalledTimes(1);
    const [payload, targetOrigin] = postSpy.mock.calls[0];
    expect(payload).toMatchObject({
      type: "sepsis-atlas:jump",
      page: 6,
      bbox: [1, 2, 3, 4],
      origin: "tl",
    });
    expect(targetOrigin).toBe("http://localhost");
  });

  it("swaps src when the stem changes", () => {
    const { rerender } = render(
      <PdfViewerPane src="http://localhost/viewer/Ren_2022?page=1" />,
    );
    rerender(<PdfViewerPane src="http://localhost/viewer/Seymour_2016?page=2" />);
    const iframe = screen.getByTitle("PDF viewer") as HTMLIFrameElement;
    expect(iframe.src).toContain("/viewer/Seymour_2016");
  });

  it("derives targetOrigin from the src URL when not passed explicitly", () => {
    const { rerender } = render(
      <PdfViewerPane src="http://example.com/viewer/Ren_2022?page=1" />,
    );
    const iframe = screen.getByTitle("PDF viewer") as HTMLIFrameElement;
    const postSpy = vi.fn();
    Object.defineProperty(iframe, "contentWindow", {
      configurable: true,
      get: () => ({ postMessage: postSpy }),
    });

    rerender(
      <PdfViewerPane src="http://example.com/viewer/Ren_2022?page=3" />,
    );

    expect(postSpy).toHaveBeenCalledTimes(1);
    expect(postSpy.mock.calls[0][1]).toBe("http://example.com");
  });

  it("re-mounts with the fresh src after src goes back to null (same stem)", () => {
    // Regression: when src goes non-null → null → non-null with the SAME stem,
    // the iframe re-mounts but the effect takes the postMessage path instead of
    // reassigning iframe.src — so a stale initialSrcRef would leave the iframe
    // pointing at the original page forever.
    const { rerender } = render(
      <PdfViewerPane src="http://localhost/viewer/Ren_2022?page=1" />,
    );
    rerender(<PdfViewerPane src={null} />);
    expect(screen.queryByTitle("PDF viewer")).not.toBeInTheDocument();
    rerender(<PdfViewerPane src="http://localhost/viewer/Ren_2022?page=6" />);
    const iframe = screen.getByTitle("PDF viewer") as HTMLIFrameElement;
    expect(iframe.src).toContain("page=6");
    expect(iframe.src).not.toContain("page=1");
  });

  it("persists the latest src to localStorage", () => {
    const { rerender } = render(
      <PdfViewerPane
        src="http://localhost/viewer/Ren_2022?page=1"
        storageKey="test_viewer_url"
      />,
    );
    expect(localStorage.getItem("test_viewer_url")).toBe(
      "http://localhost/viewer/Ren_2022?page=1",
    );
    rerender(
      <PdfViewerPane
        src="http://localhost/viewer/Seymour_2016?page=2"
        storageKey="test_viewer_url"
      />,
    );
    expect(localStorage.getItem("test_viewer_url")).toBe(
      "http://localhost/viewer/Seymour_2016?page=2",
    );
  });
});
