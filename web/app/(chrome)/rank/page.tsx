import RankPage from "@/components/RankPage";

export const metadata = { title: "Sepsis Atlas — Rank predictors" };

export default function Rank() {
  const backendUrl = (process.env.NEXT_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
  return (
    <main className="mx-auto max-w-[1100px] px-4 sm:px-7 pt-[22px] pb-[60px]">
      <RankPage backendUrl={backendUrl} />
    </main>
  );
}
