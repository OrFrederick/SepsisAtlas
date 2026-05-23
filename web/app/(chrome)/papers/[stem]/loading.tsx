export default function Loading() {
  // Shown while RSC fetches papers.json + rows.json for a stem that wasn't
  // in the build-time generateStaticParams set (i.e. a paper added since
  // the last build). Roughly mirrors the eventual layout so the page
  // doesn't shift on first paint.
  return (
    <div className="split-shell p-3">
      <div className="text-fg-muted text-[13px]">Loading paper…</div>
    </div>
  );
}
