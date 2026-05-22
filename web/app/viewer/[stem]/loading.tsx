export default function Loading() {
  // Iframe target — host shell renders an empty page while the React PDF
  // viewer hydrates. Don't add a topbar or chrome here; this page is shown
  // inside an iframe in chat / paper-detail shells.
  return (
    <div style={{ height: "100vh", display: "grid", placeItems: "center", color: "var(--fg-muted)" }}>
      Loading PDF…
    </div>
  );
}
