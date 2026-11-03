import { describe, expect, it } from "vitest";

import { needsSvgRepair, svgStatusLabel } from "./bpmnSvg";

describe("BPMN SVG presentation", () => {
  it("redraws a missing or stale SVG only when the current BPMN hash is known", () => {
    expect(needsSvgRepair({ status: "missing", current_bpmn_sha256: "abc" })).toBe(true);
    expect(needsSvgRepair({ status: "stale", current_bpmn_sha256: "abc" })).toBe(true);
    expect(needsSvgRepair({ status: "synced", current_bpmn_sha256: "abc" })).toBe(false);
    expect(needsSvgRepair({ status: "missing" })).toBe(false);
    expect(needsSvgRepair(undefined)).toBe(false);
  });

  it("names the BPMN elements missing from the drawing", () => {
    expect(svgStatusLabel({ status: "synced" })).toBe("SVG синхронізовано");
    expect(svgStatusLabel({ status: "incomplete", missing: ["Task_1", "Flow_2"] })).toBe("SVG неповний: Task_1, Flow_2");
    expect(svgStatusLabel(undefined)).toBe("SVG відсутній");
  });
});
