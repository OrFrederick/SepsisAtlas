"use client";

import type { RankFilters } from "../lib/rank";

type Props = {
  value: RankFilters;
  onChange: (next: RankFilters) => void;
  onSubmit: () => void;
  disabled: boolean;
};

const LABEL = "flex flex-col gap-[2px] text-xs";
const FIELD =
  "bg-panel border border-border rounded px-[6px] py-1 outline-none focus:border-accent";

export default function RankForm({ value, onChange, onSubmit, disabled }: Props) {
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit();
      }}
      className="flex flex-wrap gap-2 mb-3 items-end"
    >
      <label className={LABEL}>
        Outcome type
        <select
          value={value.outcomeType}
          onChange={(e) => onChange({ ...value, outcomeType: e.target.value })}
          className={FIELD}
        >
          {["mortality", "readmission", "los", "organ_failure", "other"].map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      </label>
      <label className={LABEL}>
        Window (days)
        <select
          value={value.windowDays}
          onChange={(e) => onChange({ ...value, windowDays: e.target.value })}
          className={FIELD}
        >
          <option value="">any</option>
          {["28", "30", "60", "90", "180", "365"].map((d) => (
            <option key={d} value={d}>
              {d}
            </option>
          ))}
        </select>
      </label>
      <label className={LABEL}>
        Paper ref
        <input
          type="text"
          value={value.paperRef}
          placeholder="Schlapbach 2018"
          onChange={(e) => onChange({ ...value, paperRef: e.target.value })}
          className={FIELD}
        />
      </label>
      <label className={LABEL}>
        Population contains
        <input
          type="text"
          value={value.populationContains}
          placeholder="ICU / pediatric / ..."
          onChange={(e) => onChange({ ...value, populationContains: e.target.value })}
          className={FIELD}
        />
      </label>
      <label className={LABEL}>
        Top K
        <input
          type="number"
          min={1}
          max={200}
          value={value.topK}
          onChange={(e) => onChange({ ...value, topK: Number(e.target.value) || 50 })}
          className={`${FIELD} w-20`}
        />
      </label>
      <button
        type="submit"
        disabled={disabled}
        className="bg-accent text-white border-0 py-[6px] px-[14px] rounded font-semibold cursor-pointer hover:bg-accent-hover disabled:opacity-50 disabled:cursor-default"
      >
        Rank
      </button>
    </form>
  );
}
