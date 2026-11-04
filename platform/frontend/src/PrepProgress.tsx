import type { Row } from "./api";
import { formatMs, prepAt, prepSummary } from "./standPrep";

/**
 * The stand preparing itself before the first test phase: live, at moment ``at`` of a replay, or as a
 * summary once the run is past it. ``onSkip`` jumps the replay to the start of the scenario.
 */
export function PrepProgress({ run, at, onSkip }: { run: Row | null | undefined; at?: number | null; onSkip?: (t: number) => void }) {
  const summary = prepSummary(run);
  if (!run || !summary) return null;
  const now = at ?? Date.now();
  const during = now >= summary.start && (summary.end == null || now < summary.end);
  const moment = prepAt(run.timeline, at ?? Number.POSITIVE_INFINITY);
  const failed = summary.steps.filter((step) => step.state === "failed");
  const took = summary.end != null ? summary.end - summary.start : null;
  if (during && (at != null || !run.finished_at)) {
    const total = moment?.total || 0;
    const done = moment ? (moment.state === "start" ? moment.n - 1 : moment.n) : 0;
    return <div className={`prep-progress ${moment?.state === "failed" ? "failed" : ""}`} role="status" aria-live="polite">
      <span><b>Підготовка стенда</b>{moment ? ` · крок ${moment.n} з ${total}: ${moment.title}` : " · перші кроки"}</span>
      {total > 0 && <div className="bar" aria-hidden="true"><i style={{ width: `${Math.round((done / total) * 100)}%` }} /></div>}
      <small>минуло {formatMs(now - summary.start)}{at != null && summary.end != null ? ` · сценарій почнеться через ${formatMs(summary.end - now)}` : ""}
        {at != null && summary.end != null && onSkip && <> · <button className="link" onClick={() => onSkip(summary.end as number)}>До старту сценарію ⏭</button></>}</small>
    </div>;
  }
  if (took == null && !failed.length) return null;
  return <div className={`prep-progress ${failed.length ? "failed" : ""}`}>
    <span><b>Підготовка стенда</b>{took != null ? ` · ${formatMs(took)}` : ""}{summary.steps.length ? ` · ${summary.steps.length} кроків` : " · кроки не звітувались (план підготовки вимкнено)"}
      {failed.length ? ` · помилка: ${failed.map((step) => step.title).join(", ")}` : ""}</span>
  </div>;
}
