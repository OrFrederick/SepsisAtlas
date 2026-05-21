import PapersTable from "./PapersTable";
import type { Paper } from "../lib/types";

type Props = { papers: Paper[]; basePath: string };

export default function PapersPage({ papers, basePath }: Props) {
  if (papers.length === 0) {
    return <p style={{ color: "var(--fg-muted)" }}>No papers exported yet.</p>;
  }
  return <PapersTable papers={papers} basePath={basePath} />;
}
