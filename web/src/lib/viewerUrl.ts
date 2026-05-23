/**
 * Build a viewer URL for a paper, optionally jumping to a page + bbox.
 * Handles a base path with or without trailing slash.
 */
export function buildViewerUrl(
  base: string,
  stem: string,
  page?: number,
  bbox?: string | null,
  origin?: string,
): string {
  const b = base.endsWith("/") ? base : base + "/";
  const p = Math.max(1, Math.floor(page ?? 1));
  const stemEnc = encodeURIComponent(stem);
  let url = `${b}viewer/${stemEnc}?page=${p}`;
  if (bbox) {
    url += `&bbox=${encodeURIComponent(bbox)}&origin=${encodeURIComponent(origin || "tl")}`;
  }
  return url;
}
