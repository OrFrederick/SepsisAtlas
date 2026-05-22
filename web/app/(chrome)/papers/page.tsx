import PapersPage from "@/components/PapersPage";
import { loadPapers } from "@/lib/data";

export const metadata = { title: "Sepsis Atlas — Papers" };
export const revalidate = 3600;

export default async function Papers() {
  const papers = (await loadPapers())
    .slice()
    .sort((a, b) => (b.last_update || "").localeCompare(a.last_update || ""));
  return (
    <>
      <h1 style={{ margin: "0 0 12px", fontSize: 18 }}>Corpus ({papers.length} papers)</h1>
      <PapersPage papers={papers} basePath="/" />
    </>
  );
}
