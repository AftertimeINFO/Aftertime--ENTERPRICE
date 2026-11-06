import type { Row } from "./api";

export const E2E_MARKERS = ["e2e-passed", "e2e-failed", "e2e-active", "e2e-pending", "e2e-not-reached", "e2e-stopped"];
const ACTIVE = new Set(["queued", "claimed", "waiting", "running"]);

export function isActive(run: Row | undefined): boolean {
  return !!run && ACTIVE.has(run.status);
}

/** BPMN element id → marker class for the phase states of a run. */
export function elementMarkers(run: Row | undefined, nodes: Row[] = []): Record<string, string> {
  if (!run) return {};
  const markers: Record<string, string> = {};
  for (const item of run.states || []) {
    if (item.element) markers[item.element] = `e2e-${item.state}`;
  }
  const started = (run.states || []).some((item: Row) => item.state !== "pending");
  for (const node of nodes) {
    if (node.block_type === "start_event" && started) markers[node.node_key] = "e2e-passed";
    if (node.block_type === "end_event" && run.status === "passed") markers[node.node_key] = "e2e-passed";
  }
  return markers;
}

export const isSimulation = (run: Row | undefined) => String(run?.center_job_id || "").startsWith("simulation-");

export function runStatusLabel(run: Row | undefined): string {
  if (!run) return "Прогонів ще не було";
  const labels: Record<string, string> = {
    queued: "Очікує мосту E2E",
    claimed: "Міст прийняв запит",
    waiting: run.queue_ahead ? `У черзі центру E2E, попереду ${run.queue_ahead}` : "У черзі центру E2E",
    running: "Виконується",
    passed: "Пройдено",
    failed: "Збій",
    stopped: "Зупинено",
    error: `Помилка: ${run.error || ""}`,
  };
  const label = labels[run.status] || run.status;
  // a bridge started with --simulate times the phases itself: nothing ran on the stand
  return isSimulation(run) ? `Симуляція (тест не виконувався) · ${label}` : label;
}

export function phaseSummary(run: Row | undefined): string {
  const states = run?.states || [];
  const passed = states.filter((item: Row) => item.state === "passed").length;
  return `${passed}/${states.length} фаз`;
}

/** Index of the first frame taken while the phase was running (or ended in it), -1 if none. */
export function firstFrameOfPhase(frames: Row[], phase: string): number {
  return frames.findIndex((frame) => frame.phase === phase);
}
