import { describe, expect, it } from "vitest";

import { elementMarkers, isActive, phaseSummary, runStatusLabel, standLabel } from "./e2eRun";

describe("standLabel", () => {
  it("names the configuration the run was executed on", () => {
    expect(standLabel({ stand: { version: "1.75.0.2" } })).toBe("Конфігурація 1.75.0.2");
    expect(standLabel({ stand: { version: "1.75.0.2", end: { version: "1.75.0.3" }, changed: true } }))
      .toBe("Конфігурація 1.75.0.2 → 1.75.0.3 (змінилась під час прогону)");
    expect(standLabel({ stand: { error: "down" } })).toBe("Версію конфігурації не прочитано");
    expect(standLabel({ status: "passed" })).toBe("");
  });
});

const nodes = [
  { node_key: "start", block_type: "start_event" },
  { node_key: "order", block_type: "user_task" },
  { node_key: "end", block_type: "end_event" },
];

describe("E2E run on the diagram", () => {
  it("paints each phase on its BPMN element and the start once the run began", () => {
    const run = { status: "running", states: [
      { phase: "order", element: "order", state: "passed" },
      { phase: "issue", element: "issue_task", state: "active" },
      { phase: "pay", element: null, state: "pending" },
    ] };
    expect(elementMarkers(run, nodes)).toEqual({ order: "e2e-passed", issue_task: "e2e-active", start: "e2e-passed" });
  });

  it("closes the end event only when the case passed", () => {
    const states = [{ phase: "order", element: "order", state: "passed" }];
    expect(elementMarkers({ status: "passed", states }, nodes).end).toBe("e2e-passed");
    expect(elementMarkers({ status: "failed", states }, nodes).end).toBeUndefined();
    expect(elementMarkers({ status: "queued", states: [{ phase: "order", element: "order", state: "pending" }] }, nodes).start).toBeUndefined();
  });

  it("describes the run for the operator", () => {
    expect(isActive({ status: "waiting" })).toBe(true);
    expect(isActive({ status: "passed" })).toBe(false);
    expect(runStatusLabel({ status: "waiting", queue_ahead: 2 })).toBe("У черзі центру E2E, попереду 2");
    expect(runStatusLabel(undefined)).toBe("Прогонів ще не було");
    expect(phaseSummary({ states: [{ state: "passed" }, { state: "active" }] })).toBe("1/2 фаз");
  });
});

describe("VM screen frames", () => {
  it("finds the first frame of a phase", async () => {
    const { firstFrameOfPhase } = await import("./e2eRun");
    const frames = [{ phase: null }, { phase: "order" }, { phase: "order" }, { phase: "issue" }];
    expect(firstFrameOfPhase(frames, "order")).toBe(1);
    expect(firstFrameOfPhase(frames, "issue")).toBe(3);
    expect(firstFrameOfPhase(frames, "pay")).toBe(-1);
  });
});
