import { describe, expect, it } from "vitest";

import { hasSystemModel, inPack, selectablePacks } from "./packs";

const packs = [
  { id: "energy", installed: true, enabled: true, projects: ["model-lab-energy"], workflows: ["storm-restoration"] },
  { id: "rental", installed: true, enabled: true, projects: [], workflows: ["rental-issue"] },
  { id: "example", installed: false, enabled: false, projects: [], workflows: [] },
  { id: "old", installed: true, enabled: false, projects: ["p"], workflows: [] },
];

describe("model packs in the interface", () => {
  it("offers only installed, enabled packs that have something to show", () => {
    expect(selectablePacks(packs).map((pack) => pack.id)).toEqual(["energy", "rental"]);
  });

  it("filters processes and projects by the chosen pack", () => {
    const versions = [{ workflow_id: "storm-restoration" }, { workflow_id: "rental-issue" }];
    expect(inPack(versions, packs[1], "workflows", (row) => row.workflow_id)).toEqual([{ workflow_id: "rental-issue" }]);
    expect(inPack(versions, undefined, "workflows", (row) => row.workflow_id)).toHaveLength(2);
    expect(inPack([{ id: "model-lab-energy" }, { id: "x" }], packs[0], "projects", (row) => row.id)).toEqual([{ id: "model-lab-energy" }]);
  });

  it("knows when a pack has no system model", () => {
    expect(hasSystemModel(packs[0])).toBe(true);
    expect(hasSystemModel(packs[1])).toBe(false);
    expect(hasSystemModel(undefined)).toBe(true);
  });
});
