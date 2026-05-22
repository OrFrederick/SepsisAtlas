import RankPage from "@/components/RankPage";

export const metadata = { title: "Sepsis Atlas — Rank predictors" };

export default function Rank() {
  const backendUrl = (process.env.NEXT_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");
  return <RankPage backendUrl={backendUrl} />;
}
