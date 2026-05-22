import type { FeedbackPayload } from "./feedback-schema";

export interface GithubDeps {
  fetch: typeof fetch;
  repo: string;   // "owner/name"
  token: string;
}

const BASE_LABELS = ["feedback", "from-website", "needs-triage"];

function labelsFor(payload: FeedbackPayload): string[] {
  const labels = [...BASE_LABELS, `feedback:${payload.type}`];
  if (payload.paperStem) labels.push(`paper:${payload.paperStem}`);
  return labels;
}

function bodyFor(payload: FeedbackPayload): string {
  const lines = [
    `**Type:** ${payload.type}`,
    `**Paper:** ${payload.paperStem ?? "n/a"}`,
    "**Row context:**",
    "```json",
    payload.rowContext !== undefined ? JSON.stringify(payload.rowContext, null, 2) : "n/a",
    "```",
    `**Contact:** ${payload.contact ?? "anon"}`,
    `**Submitted:** ${new Date().toISOString()}`,
    "",
    "---",
    "",
    payload.body,
    "",
    "---",
    "*Submitted via SepsisAtlas feedback form. No IP or user-agent stored.*",
  ];
  return lines.join("\n");
}

async function ensurePaperLabel(deps: GithubDeps, stem: string): Promise<void> {
  const url = `https://api.github.com/repos/${deps.repo}/labels`;
  const res = await deps.fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${deps.token}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      name: `paper:${stem}`,
      color: "ededed",
      description: `Issues referencing paper ${stem}`,
    }),
  });
  if (res.status === 201) return;
  if (res.status === 422) {
    const data = await res.json().catch(() => ({}));
    const errs = (data as { errors?: { code?: string }[] }).errors ?? [];
    if (errs.some((e) => e.code === "already_exists")) return;
  }
  // Don't throw on label-create failure: missing paper:* label shouldn't
  // block the actual issue creation. Log and move on.
  console.warn(`[github] ensurePaperLabel for ${stem} returned ${res.status}`);
}

export async function createFeedbackIssue(
  payload: FeedbackPayload,
  deps: GithubDeps,
): Promise<{ issueUrl: string }> {
  if (payload.paperStem) {
    await ensurePaperLabel(deps, payload.paperStem);
  }
  const url = `https://api.github.com/repos/${deps.repo}/issues`;
  const res = await deps.fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${deps.token}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      title: `[feedback:${payload.type}] ${payload.title}`,
      body: bodyFor(payload),
      labels: labelsFor(payload),
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`github issues POST ${res.status}: ${text}`);
  }
  const data = (await res.json()) as { html_url: string };
  return { issueUrl: data.html_url };
}

export const __testing = { labelsFor, bodyFor };
