import PapersPage from "@/components/PapersPage";
import { loadPapers } from "@/lib/data";

export const metadata = { title: "Sepsis Atlas — Papers" };
export const revalidate = 3600;

export default async function Papers() {
  const papers = (await loadPapers())
    .slice()
    .sort((a, b) => (b.last_update || "").localeCompare(a.last_update || ""));
  return (
    <main className="mx-auto max-w-[1100px] px-7 pt-[22px] pb-[60px]">
      <h1 className="m-0 mb-3 text-lg">Corpus ({papers.length} papers)</h1>
      <PapersPage papers={papers} basePath="/" />
    </main>
  );
}
