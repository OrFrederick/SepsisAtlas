import type { RankFilters } from "../lib/rank";

type Props = {
  value: RankFilters;
  onChange: (next: RankFilters) => void;
  onSubmit: () => void;
  disabled: boolean;
};

export default function RankForm({ value, onChange, onSubmit, disabled }: Props) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12, alignItems: "end" }}
    >
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Outcome type
        <select
          value={value.outcomeType}
          onChange={(e) => onChange({ ...value, outcomeType: e.target.value })}
          style={{ padding: "4px 6px" }}
        >
          {["mortality", "readmission", "los", "organ_failure", "other"].map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Window (days)
        <select
          value={value.windowDays}
          onChange={(e) => onChange({ ...value, windowDays: e.target.value })}
          style={{ padding: "4px 6px" }}
        >
          <option value="">any</option>
          {["28", "30", "60", "90", "180", "365"].map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Paper ref
        <input
          type="text"
          value={value.paperRef}
          placeholder="Schlapbach 2018"
          onChange={(e) => onChange({ ...value, paperRef: e.target.value })}
          style={{ padding: "4px 6px" }}
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Population contains
        <input
          type="text"
          value={value.populationContains}
          placeholder="ICU / pediatric / ..."
          onChange={(e) => onChange({ ...value, populationContains: e.target.value })}
          style={{ padding: "4px 6px" }}
        />
      </label>
      <label style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 12 }}>
        Top K
        <input
          type="number"
          min={1}
          max={200}
          value={value.topK}
          onChange={(e) => onChange({ ...value, topK: Number(e.target.value) || 50 })}
          style={{ padding: "4px 6px", width: 80 }}
        />
      </label>
      <button type="submit" disabled={disabled} style={{ padding: "6px 14px", fontWeight: 600 }}>
        Rank
      </button>
    </form>
  );
}
