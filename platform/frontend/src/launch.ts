import type { Row } from "./api";
import { formatDuration } from "./reports";

const NEED_LABELS: Record<string, string> = { terminal: "термінал", prro: "ПРРО", scanner: "сканер" };

/** "термінал: virtual · ПРРО: virtual · застава after" from a suite's stand requirements. */
export function needsSummary(needs: Row | undefined): string {
  if (!needs) return "";
  const parts = Object.entries(NEED_LABELS)
    .filter(([key]) => needs[key] && needs[key] !== "manual")
    .map(([key, label]) => `${label}: ${needs[key]}`);
  if (needs.depositMode) parts.push(`застава ${needs.depositMode}`);
  return parts.join(" · ");
}

export function launchTitle(run: Row): string {
  if (run.kind === "center") return run.target?.title || [...(run.target?.cases || []), ...(run.target?.suites || [])].join(", ");
  return `Процес · ${run.binding}`;
}

/** Progress bar and a sentence from the control center's progress (seconds, epoch seconds). */
export function progressView(progress: Row | null | undefined): { pct: number; text: string } | null {
  if (!progress) return null;
  const parts = [`${progress.suitesDone ?? 0}/${progress.suitesTotal ?? 0} наборів`];
  if (progress.casesTotal) parts.push(`${progress.casesDone ?? 0}/${progress.casesTotal} кейсів`);
  if (progress.elapsed != null) parts.push(`минуло ${formatDuration(progress.elapsed * 1000)}`);
  if (progress.remaining != null) parts.push(`залишилось ≈${formatDuration(progress.remaining * 1000)}`);
  if (progress.eta) parts.push(`завершення ≈${new Date(progress.eta * 1000).toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" })}`);
  if (progress.over) parts.push("довше, ніж зазвичай");
  return { pct: Math.max(0, Math.min(100, progress.pct ?? 0)), text: parts.join(" · ") };
}
