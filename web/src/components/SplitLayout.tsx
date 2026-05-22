import "./SplitLayout.css";

type Props = {
  left: React.ReactNode;
  right: React.ReactNode;
};

export default function SplitLayout({ left, right }: Props) {
  return (
    <div className="split-shell">
      <section className="split-left">{left}</section>
      <div className="split-divider" />
      <section className="split-right">{right}</section>
    </div>
  );
}
