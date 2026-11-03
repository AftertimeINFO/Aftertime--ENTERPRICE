import type { Row } from "./api";

// Elements a token waits on in a run; only these can mirror a test phase.
const PHASE_TYPES = new Set([
  "bpmn:Task",
  "bpmn:ServiceTask",
  "bpmn:UserTask",
  "bpmn:ManualTask",
  "bpmn:ScriptTask",
  "bpmn:BusinessRuleTask",
  "bpmn:SendTask",
  "bpmn:ReceiveTask",
  "bpmn:IntermediateCatchEvent",
]);

export function acceptsTestPhase(type: string): boolean {
  return PHASE_TYPES.has(type);
}

export function phaseOptions(bindings: Row[], bindingId: string | undefined): string[] {
  return bindings.find((binding) => binding.id === bindingId)?.phases || [];
}

/** Latest version of every workflow, test-bound processes first, then by name. */
export function processCatalog(workflows: Row[]): Row[] {
  const latest = new Map<string, Row>();
  for (const version of workflows) {
    const current = latest.get(version.workflow_id);
    if (!current || version.version > current.version) latest.set(version.workflow_id, version);
  }
  return [...latest.values()]
    .map((version): Row => ({ ...version, tested: /aftertime:testBinding="[^"]+"/.test(version.bpmn_xml || "") }))
    .sort((left, right) => Number(right.tested) - Number(left.tested) || String(left.name).localeCompare(String(right.name), "uk"));
}
