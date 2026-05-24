export default function Loading() {
  // Shown while RSC fetches papers.json + rows.json for a stem that wasn't
  // in the build-time generateStaticParams set (i.e. a paper added since
  // the last build). Roughly mirrors the eventual layout so the page
  // doesn't shift on first paint.
  return (
    <div className="split-shell" style={{ padding: 12 }}>
      <div style={{ color: "var(--fg-muted)", fontSize: 13 }}>Loading paper…</div>
    </div>
  );
}
