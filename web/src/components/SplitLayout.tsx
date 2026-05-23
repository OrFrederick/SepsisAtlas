"use client";

type Props = {
  left: React.ReactNode;
  right: React.ReactNode;
};

// `split-shell` is a marker class with no attached CSS — kept so future
// selectors (tests, body:has() rules) can target this layout without
// coupling to internal class names. The 900 px breakpoint is the threshold
// at which the side-by-side layout starts crowding the 480 px left rail;
// below it, the panels stack and the PDF claims 60vh.
export default function SplitLayout({ left, right }: Props) {
  return (
    <main className="split-shell grid w-full grid-cols-1 grid-rows-[auto_1px_60vh] h-auto min-[900px]:grid-cols-[480px_1px_1fr] min-[900px]:grid-rows-none min-[900px]:h-[calc(100vh-49px)]">
      <section className="overflow-y-auto overflow-x-hidden pt-[14px] pb-[60px] px-4 bg-bg">
        {left}
      </section>
      <div className="bg-border w-full h-px min-[900px]:h-full min-[900px]:w-px" />
      <section className="relative bg-panel-2 overflow-hidden [&_iframe]:w-full [&_iframe]:h-full [&_iframe]:border-0 [&_iframe]:bg-panel-2">
        {right}
      </section>
    </main>
  );
}
