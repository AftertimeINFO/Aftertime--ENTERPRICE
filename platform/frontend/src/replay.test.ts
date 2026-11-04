import { describe, expect, it } from "vitest";

import { formatOffset, frameAt, phaseMoments, replayBounds, replayStates } from "./replay";

const at = (second: number) => new Date(Date.UTC(2026, 10, 4, 10, 0, second)).toISOString();
const run = {
  claimed_at: at(0),
  finished_at: at(50),
  status: "failed",
  states: [
    { phase: "order", element: "order", state: "passed" },
    { phase: "issue", element: "issue", state: "failed" },
    { phase: "pay", element: "pay", state: "not-reached" },
  ],
  timeline: [
    { t: at(1), status: "running" },
    { t: at(10), phase: "order", state: "active" },
    { t: at(20), phase: "order", state: "passed" },
    { t: at(20), phase: "issue", state: "active" },
    { t: at(50), phase: "issue", state: "failed" },
  ],
};
const frames = [
  { id: "f1", captured_at: at(12), phase: "order" },
  { id: "f2", captured_at: at(25), phase: "issue" },
  { id: "f3", captured_at: at(49), phase: "issue" },
];
const states = (items: Record<string, any>[]) => items.map((item) => `${item.phase}=${item.state}`);

describe("replaying a run", () => {
  const bounds = replayBounds(run, frames)!;

  it("spans from the claim to the end of the run", () => {
    expect(bounds.end - bounds.start).toBe(50_000);
    expect(formatOffset(bounds.start + 83_000, bounds)).toBe("1:23");
  });

  it("replays phase states from the recorded timeline", () => {
    expect(states(replayStates(run, frames, Date.parse(at(5)), bounds))).toEqual(["order=pending", "issue=pending", "pay=pending"]);
    expect(states(replayStates(run, frames, Date.parse(at(15)), bounds))).toEqual(["order=active", "issue=pending", "pay=pending"]);
    expect(states(replayStates(run, frames, Date.parse(at(30)), bounds))).toEqual(["order=passed", "issue=active", "pay=pending"]);
    expect(states(replayStates(run, frames, bounds.end, bounds))).toEqual(["order=passed", "issue=failed", "pay=not-reached"]);
  });

  it("falls back to the phases written on frames for runs without a timeline", () => {
    const old = { ...run, timeline: [] };
    expect(states(replayStates(old, frames, Date.parse(at(30)), bounds))).toEqual(["order=passed", "issue=active", "pay=pending"]);
    expect(phaseMoments(old, frames).map((item) => item.phase)).toEqual(["order", "issue"]);
  });

  it("shows the last frame taken up to the moment and jumps phase by phase", () => {
    expect(frameAt(frames, Date.parse(at(30)))).toBe(1);
    expect(frameAt(frames, Date.parse(at(0)))).toBe(-1);
    expect(phaseMoments(run, frames).map((item) => item.phase)).toEqual(["order", "issue"]);
  });
});

describe("screen video under a replay", () => {
  it("maps the replayed moment onto the packed video", async () => {
    const { videoTime, replayBounds } = await import("./replay");
    const recorded = { ...run, recording: { status: "ready", started_at: at(5), duration_ms: 40_000 } };
    expect(videoTime(recorded, Date.parse(at(15)))).toBe(10);
    expect(videoTime(recorded, Date.parse(at(2)))).toBeNull();
    expect(videoTime({ ...recorded, recording: { status: "packing", started_at: at(5) } }, Date.parse(at(15)))).toBeNull();
    expect(replayBounds(recorded, [])!.end).toBe(Date.parse(at(50)));
  });
});

describe("jumping to a phase", () => {
  it("reaches a phase that was proven without ever being active", async () => {
    const { phaseMoments } = await import("./replay");
    const quick = { ...run, timeline: [
      { t: at(10), phase: "order", state: "active" },
      { t: at(20), phase: "order", state: "passed" },
      { t: at(20), phase: "issue", state: "passed" },
      { t: at(30), phase: "pay", state: "active" },
    ] };
    expect(phaseMoments(quick, []).map((item) => [item.phase, item.t])).toEqual([
      ["order", Date.parse(at(10))], ["issue", Date.parse(at(20))], ["pay", Date.parse(at(30))],
    ]);
  });
});
