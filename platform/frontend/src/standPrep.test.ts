import { describe, expect, it } from "vitest";

import {
  addStep, conditionsOf, planText, describeWhen, diffExcerpt, estimateMs, expand, lineDiff, lineOf, moveStep, prepAt, prepSummary,
  removeStep, skipReason, updateStep, varsFor, whenOf, type Plan,
} from "./standPrep";

const plan: Plan = {
  vars: { regionKey: { from: "region", default: "north" }, mode: { from: "pay", map: { card: "card" }, default: "cash" } },
  stages: [
    { id: "machine", title: "Machine", fixed: true, steps: [{ id: "check", title: "Check", find: "check" }] },
    { id: "data", title: "Data", steps: [
      { id: "clean", title: "Clean", script: "clean.ps1", scope: "suite" },
      { id: "accounts", title: "Accounts ({regionKey})", script: "acc.ps1", when: { web: false, region: "!south" } },
      { id: "web", title: "Web shop", script: "web.ps1", when: { web: true } },
      { id: "off", title: "Off", script: "off.ps1", enabled: false },
    ] },
  ],
};

describe("the preparation plan of the stand", () => {
  it("picks the steps of a case as the stand does and says why the others are left out", () => {
    const north = { id: "a", web: false, region: null };
    const shop = { id: "b", web: true, region: "south" };
    const steps = plan.stages[1].steps;
    expect(steps.filter((step) => !skipReason(step, north)).map((step) => step.id)).toEqual(["clean", "accounts"]);
    expect(steps.filter((step) => !skipReason(step, shop)).map((step) => step.id)).toEqual(["clean", "web"]);
    expect(skipReason(steps[1], shop)).toBe("лише коли web не задано");
    expect(skipReason(steps[3], north)).toBe("крок вимкнено");
    expect(describeWhen(steps[1].when)).toBe("web не задано і region ≠ south");
  });

  it("computes the variables of a case and fills them into titles", () => {
    expect(varsFor(plan, { id: "a", region: "", pay: "card" })).toEqual({ regionKey: "north", mode: "card", case: "a" });
    expect(varsFor(plan, { id: "b", region: "east", pay: "other" })).toMatchObject({ regionKey: "east", mode: "cash" });
    expect(expand("Accounts ({regionKey}) {unknown}", { regionKey: "east" })).toBe("Accounts (east) {unknown}");
  });

  it("turns conditions into a form and back without losing them", () => {
    const when = { web: false, region: "!south", pay: "card", gift: true };
    expect(whenOf(conditionsOf(when))).toEqual(when);
    expect(whenOf([{ field: "", op: "eq", value: "x" }])).toBeUndefined();
  });

  it("edits, moves, adds and removes steps without touching the original", () => {
    const moved = moveStep(plan, "data", "accounts", -1);
    expect(moved.stages[1].steps.map((step) => step.id)).toEqual(["accounts", "clean", "web", "off"]);
    expect(moveStep(plan, "data", "clean", -1)).toEqual(plan);
    const edited = updateStep(plan, "data", "clean", { enabled: false, scope: undefined });
    expect(edited.stages[1].steps[0]).toEqual({ id: "clean", title: "Clean", script: "clean.ps1", enabled: false });
    const added = addStep(plan, "data");
    expect(added.id).toBe("step-1");
    expect(removeStep(added.plan, "data", "step-1")).toEqual(plan);
    expect(plan.stages[1].steps[0].scope).toBe("suite");
  });

  it("estimates the preparation of a case from the times the stand reported", () => {
    expect(estimateMs(plan, { id: "a", web: false }, { clean: { mean_ms: 1000 }, accounts: { mean_ms: 500 }, web: { mean_ms: 9000 } })).toBe(1500);
    expect(estimateMs(plan, { id: "a", web: false }, {})).toBeNull();
  });

  it("writes the plan in the layout of the stand's file: one line per step and variable", () => {
    expect(planText({ enabled: false, vars: { k: { from: "region", map: { a: "b" } } }, stages: [
      { id: "s", title: "S", steps: [{ id: "x", title: "X", args: ["-Mode", "apply"], when: { web: false } }, { id: "y", title: "Y" }] },
      { id: "e", title: "E", steps: [] },
    ] })).toBe([
      "{", '  "enabled": false,', '  "vars": {', '    "k": { "from": "region", "map": { "a": "b" } }', "  },", '  "stages": [', "    {",
      '      "id": "s",', '      "title": "S",', '      "steps": [',
      '        { "id": "x", "title": "X", "args": ["-Mode", "apply"], "when": { "web": false } },', '        { "id": "y", "title": "Y" }', "      ]", "    },",
      "    {", '      "id": "e",', '      "title": "E",', '      "steps": []', "    }", "  ]", "}", "",
    ].join("\n"));
  });

  it("shows what a save changes and finds a line by its text", () => {
    const diff = lineDiff("a\nb\nc\nd\ne\nf\ng", "a\nb\nC\nd\ne\nf\ng");
    expect(diff.filter((line) => line.kind !== " ")).toEqual([{ kind: "-", text: "c" }, { kind: "+", text: "C" }]);
    expect(diffExcerpt(diff, 1).map((line) => line && line.kind + line.text)).toEqual([null, " b", "-c", "+C", " d", null]);
    expect(lineOf("one\ntwo\nthree", "thr")).toBe(2);
    expect(lineOf("one", "missing")).toBe(-1);
  });

  it("replays the preparation of a run step by step", () => {
    const run = { timeline: [
      { t: "2026-11-04T10:00:00Z", status: "running" },
      { t: "2026-11-04T10:00:05Z", prep: { state: "start", n: 1, total: 2, step: "clean", title: "Clean" } },
      { t: "2026-11-04T10:00:09Z", prep: { state: "ok", n: 1, total: 2, step: "clean", title: "Clean", ms: 4000 } },
      { t: "2026-11-04T10:00:10Z", prep: { state: "ok", n: 2, total: 2, step: "accounts", title: "Accounts", ms: 900 } },
      { t: "2026-11-04T10:02:00Z", phase: "order", state: "active" },
    ] };
    expect(prepAt(run.timeline, Date.parse("2026-11-04T10:00:06Z"))).toMatchObject({ n: 1, state: "start", title: "Clean" });
    expect(prepAt(run.timeline, Date.parse("2026-11-04T10:00:01Z"))).toBeNull();
    const summary = prepSummary(run);
    expect(summary).toMatchObject({ start: Date.parse("2026-11-04T10:00:00Z"), end: Date.parse("2026-11-04T10:02:00Z") });
    expect(summary?.steps.map((step) => step.step)).toEqual(["clean", "accounts"]);
    expect(prepSummary({ timeline: [] })).toBeNull();
  });
});
