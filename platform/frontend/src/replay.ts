import type { Row } from "./api";

/** Replaying a finished E2E run: phase states and the screen frame at any moment of it. */

const ms = (value: string | undefined | null): number => (value ? new Date(value).getTime() : NaN);

export type Bounds = { start: number; end: number };

export function replayBounds(run: Row, frames: Row[]): Bounds | null {
  const times = [
    ...(run.timeline || []).map((item: Row) => ms(item.t)),
    ...frames.map((frame) => ms(frame.captured_at)),
    ms(run.claimed_at),
    ms(run.finished_at),
  ].filter((value) => !Number.isNaN(value));
  if (!times.length) return null;
  const start = Math.min(...times);
  const end = Math.max(...times);
  return end > start ? { start, end } : { start, end: start + 1000 };
}

/** Phase states at moment ``t`` (ms). At or after the end the run's final states are shown. */
export function replayStates(run: Row, frames: Row[], t: number, bounds: Bounds): Row[] {
  const final: Row[] = run.states || [];
  if (t >= bounds.end) return final;
  const current = new Map<string, string>(final.map((item) => [item.phase, "pending"]));
  const phaseEntries = (run.timeline || []).filter((item: Row) => item.phase);
  if (phaseEntries.length) {
    for (const entry of phaseEntries) if (ms(entry.t) <= t) current.set(entry.phase, entry.state);
  } else {
    // Runs recorded before the timeline existed: each frame knows the phase that was running.
    const seen = frames.filter((frame) => frame.phase && ms(frame.captured_at) <= t);
    const phase = seen.length ? seen[seen.length - 1].phase : null;
    const index = final.findIndex((item) => item.phase === phase);
    final.forEach((item, position) => {
      if (index < 0) return;
      if (position < index) current.set(item.phase, item.state === "pending" || item.state === "not-reached" ? "passed" : item.state);
      if (position === index) current.set(item.phase, "active");
    });
  }
  return final.map((item) => ({ ...item, state: current.get(item.phase) || "pending" }));
}

/** Index of the frame on screen at moment ``t``: the last one taken until then, -1 before the first. */
export function frameAt(frames: Row[], t: number): number {
  let found = -1;
  frames.forEach((frame, index) => { if (ms(frame.captured_at) <= t) found = index; });
  return found;
}

/** Moments when a phase starts (becomes active), for jumping phase by phase. */
export function phaseMoments(run: Row, frames: Row[]): { t: number; phase: string }[] {
  const fromTimeline = (run.timeline || [])
    .filter((item: Row) => item.phase && item.state === "active")
    .map((item: Row) => ({ t: ms(item.t), phase: item.phase }));
  if (fromTimeline.length) return fromTimeline;
  const moments: { t: number; phase: string }[] = [];
  for (const frame of frames) {
    if (frame.phase && moments[moments.length - 1]?.phase !== frame.phase) moments.push({ t: ms(frame.captured_at), phase: frame.phase });
  }
  return moments;
}

export function formatOffset(t: number, bounds: Bounds): string {
  const seconds = Math.max(0, Math.round((t - bounds.start) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
