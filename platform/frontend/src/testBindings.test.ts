import { describe, expect, it } from "vitest";

import { acceptsTestPhase, phaseOptions, processCatalog } from "./testBindings";

describe("test bindings", () => {
  it("allows phases only on elements a token waits on", () => {
    expect(acceptsTestPhase("bpmn:UserTask")).toBe(true);
    expect(acceptsTestPhase("bpmn:IntermediateCatchEvent")).toBe(true);
    expect(acceptsTestPhase("bpmn:ExclusiveGateway")).toBe(false);
    expect(acceptsTestPhase("bpmn:StartEvent")).toBe(false);
  });

  it("suggests the phases of the chosen binding", () => {
    const bindings = [{ id: "rental", phases: ["order", "issue"] }];
    expect(phaseOptions(bindings, "rental")).toEqual(["order", "issue"]);
    expect(phaseOptions(bindings, undefined)).toEqual([]);
  });

  it("lists the latest version of each process, test-bound ones first", () => {
    const catalog = processCatalog([
      { id: "a1", workflow_id: "a", version: 1, name: "Аварійний ремонт", bpmn_xml: "" },
      { id: "b1", workflow_id: "b", version: 1, name: "Видача", bpmn_xml: '<bpmn:process aftertime:testBinding="rental">' },
      { id: "a2", workflow_id: "a", version: 2, name: "Аварійний ремонт", bpmn_xml: "" },
    ]);
    expect(catalog.map((item) => [item.id, item.tested])).toEqual([["b1", true], ["a2", false]]);
  });
});
