export default function Loading() {
  // Iframe target — host shell renders an empty page while the React PDF
  // viewer hydrates. Don't add a topbar or chrome here; this page is shown
  // inside an iframe in chat / paper-detail shells.
  return (
    <div className="h-screen grid place-items-center text-fg-muted">
      Loading PDF…
    </div>
  );
}
