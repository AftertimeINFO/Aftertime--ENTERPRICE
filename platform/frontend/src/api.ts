const API = import.meta.env.VITE_API_URL || "/api/v1";
export type Row = Record<string, any>;
export type PortContract = { key: string; name: string; direction: "input" | "output" | "bidirectional"; domain: string; role: string; quantity: string; unit: string; required: boolean; multiple: boolean; variables: Row[]; capabilities: string[] };
export type Observable = { address: string; scope: "component" | "port" | "connection" | "system"; target_id: string; target_key: string; target_name: string; metric_key: string; name: string; unit: string; value_type: string; path: string; recording: string; aggregation: string };
import type { EventAction, EventProgram } from "./eventDesigner";
export type Workspace = { project: Row; projects: Row[]; version: Row; models: Row[]; components: (Row & { ports: PortContract[] })[]; nodes: Row[]; edges: Row[]; scenarios: Row[]; resources: Row[]; runs: Row[]; scripts: Row[]; workflows: Row[]; metrics: Row[]; dashboards: Row[]; monitors: Row[]; event_types: Row[]; event_programs: EventProgram[]; observables: Observable[] };
export async function request<T>(path: string, init?: RequestInit, timeoutMs = 20_000): Promise<T> {
  const timeout = AbortSignal.timeout(timeoutMs);
  let response: Response;
  try {
    response = await fetch(`${API}${path}`, { ...init, signal: init?.signal || timeout, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") throw new Error("Сервер не відповів за 20 секунд. Перевірте стан API/worker.");
    throw error;
  }
  if (!response.ok) {
    const text = await response.text();
    let payload: any;
    try { payload = JSON.parse(text); } catch { payload = null; }
    const detail = payload?.detail;
    if (typeof detail === "string") throw new Error(detail);
    if (detail?.errors) throw new Error(detail.errors.join(". "));
    if (detail?.message) throw new Error(detail.message);
    throw new Error(text || `${response.status}`);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
const json = (method: string, body?: unknown): RequestInit => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });
export const api = {
  workspace: (project?: string) => request<Workspace>(`/lab/workspace${project ? `?project_id=${encodeURIComponent(project)}` : ""}`),
  createProject: (body: Row) => request<Row>("/lab/projects", json("POST", body)),
  updateDashboard: (id: string, body: Row) => request<Row>(`/lab/dashboards/${id}`, json("PATCH", body)),
  graph: (version: string) => request<Row>(`/lab/versions/${version}/graph`),
  workflow: (version: string) => request<Row>(`/lab/workflows/${version}`),
  saveWorkflowBpmn: (version: string, bpmn_xml: string, bpmn_svg?: string) => request<Row>(`/lab/workflows/${version}/bpmn`, json("PUT", { bpmn_xml, ...(bpmn_svg ? { bpmn_svg } : {}) })),
  saveWorkflowSvg: (version: string, bpmn_svg: string, bpmn_sha256: string) => request<Row>(`/lab/workflows/${version}/svg`, json("PUT", { bpmn_svg, bpmn_sha256 })),
  workflowSvgUrl: (version: string) => `${API}/lab/workflows/${version}/svg`,
  testBindings: () => request<Row[]>("/lab/test-bindings"),
  e2eRuns: (version: string) => request<Row[]>(`/lab/workflows/${version}/e2e-runs`),
  startE2eRun: (version: string) => request<Row>(`/lab/workflows/${version}/e2e-runs`, json("POST")),
  e2eRun: (id: string, start = 0) => request<Row>(`/lab/e2e-runs/${id}?start=${start}`),
  e2eFrames: (id: string) => request<Row[]>(`/lab/e2e-runs/${id}/frames`),
  e2eFrameUrl: (id: string) => `${API}/lab/e2e-frames/${encodeURIComponent(id)}`,
  stopE2eRun: (id: string) => request<Row>(`/lab/e2e-runs/${id}/stop`, json("POST")),
  reportLaunches: () => request<Row[]>("/lab/reports/launches"),
  reportOverview: (launchId?: string, includeArchived = false) => request<Row>(`/lab/reports/overview?${new URLSearchParams({ ...(launchId ? { launch_id: launchId } : {}), ...(includeArchived ? { include_archived: "true" } : {}) })}`),
  reportResults: (filters: Row) => request<Row[]>(`/lab/reports/results?${new URLSearchParams(Object.entries(filters).filter(([, value]) => value) as [string, string][])}`),
  reportResult: (id: string) => request<Row>(`/lab/reports/results/${encodeURIComponent(id)}`),
  archiveTest: (history_id: string, name: string) => request<Row>("/lab/reports/archive", json("POST", { history_id, name })),
  restoreTest: (historyId: string) => request<Row>(`/lab/reports/archive/${encodeURIComponent(historyId)}`, json("DELETE")),
  packs: () => request<Row[]>("/lab/packs"),
  settings: () => request<Row>("/lab/settings"),
  installPack: (id: string) => request<Row>(`/lab/packs/${encodeURIComponent(id)}/install`, json("POST")),
  centerState: () => request<Row>("/lab/center/state"),
  centerRuns: () => request<Row[]>("/lab/center/runs"),
  startCenterRun: (body: Row) => request<Row>("/lab/center/runs", json("POST", body)),
  reportAttachmentUrl: (source: string) => `${API}/lab/reports/attachments/${encodeURIComponent(source)}`,
  createWorkflow: (body: Row) => request<Row>("/lab/workflows", json("POST", body)),
  updateWorkflow: (version: string, body: Row) => request<Row>(`/lab/workflows/${version}`, json("PATCH", body)),
  addWorkflowNode: (version: string, body: Row) => request<Row>(`/lab/workflows/${version}/nodes`, json("POST", body)),
  updateWorkflowNode: (id: string, body: Row) => request<Row>(`/lab/workflows/nodes/${id}`, json("PATCH", body)),
  deleteWorkflowNode: (id: string) => request<void>(`/lab/workflows/nodes/${id}`, json("DELETE")),
  addWorkflowTransition: (version: string, body: Row) => request<Row>(`/lab/workflows/${version}/transitions`, json("POST", body)),
  deleteWorkflowTransition: (id: string) => request<void>(`/lab/workflows/transitions/${id}`, json("DELETE")),
  cloneWorkflow: (version: string) => request<Row>(`/lab/workflows/${version}/clone`, json("POST")),
  validateWorkflow: (version: string) => request<Row>(`/lab/workflows/${version}/validate`, json("POST")),
  publishWorkflow: (version: string) => request<Row>(`/lab/workflows/${version}/publish`, json("POST")),
  scenarioTimeline: (id: string) => request<Row>(`/lab/scenarios/${id}/timeline`),
  scenarioAssembly: (id: string) => request<Row>(`/lab/scenarios/${id}/assembly`),
  cloneAssembly: (id: string) => request<Row>(`/lab/assemblies/${id}/clone`, json("POST")),
  processSet: (version: string) => request<Row>(`/lab/process-sets/${version}`),
  cloneProcessSet: (version: string) => request<Row>(`/lab/process-set-versions/${version}/clone`, json("POST")),
  addNode: (version: string, body: Row) => request<Row>(`/lab/versions/${version}/nodes`, json("POST", body)),
  updateNode: (id: string, body: Row) => request<Row>(`/lab/nodes/${id}`, json("PATCH", body)),
  deleteNode: (id: string) => request<void>(`/lab/nodes/${id}`, json("DELETE")),
  addEdge: (version: string, body: Row) => request<Row>(`/lab/versions/${version}/edges`, json("POST", body)),
  updateEdge: (id: string, body: Row) => request<Row>(`/lab/edges/${id}`, json("PATCH", body)),
  deleteEdge: (id: string) => request<void>(`/lab/edges/${id}`, json("DELETE")),
  observables: (version: string, target?: string) => request<{ items: Observable[] }>(`/lab/versions/${version}/observables${target ? `?target_id=${encodeURIComponent(target)}` : ""}`),
  createMonitor: (version: string, body: Row) => request<Row>(`/lab/versions/${version}/monitors`, json("POST", body)),
  updateMonitor: (id: string, body: Row) => request<Row>(`/lab/monitors/${id}`, json("PATCH", body)),
  eventPrograms: (version: string) => request<{ items: EventProgram[] }>(`/lab/versions/${version}/event-programs`),
  qualificationDefinition: (version: string) => request<{ item: Row | null }>(`/lab/versions/${version}/qualification-definition`),
  saveQualificationDefinition: (version: string, document: Row) => request<Row>(`/lab/versions/${version}/qualification-definition`, json("PUT", { document })),
  replayQualificationDefinition: (version: string) => request<Row>(`/lab/versions/${version}/qualification-definition/replay`, json("POST"), 60_000),
  createEventProgram: (version: string, body: Row) => request<EventProgram>(`/lab/versions/${version}/event-programs`, json("POST", body)),
  updateEventProgram: (id: string, body: Row) => request<EventProgram>(`/lab/event-programs/${id}`, json("PATCH", body)),
  deleteEventProgram: (id: string) => request<void>(`/lab/event-programs/${id}`, json("DELETE")),
  createEventAction: (program: string, body: Row) => request<EventAction>(`/lab/event-programs/${program}/actions`, json("POST", body)),
  updateEventAction: (id: string, body: Row) => request<EventAction>(`/lab/event-actions/${id}`, json("PATCH", body)),
  deleteEventAction: (id: string) => request<void>(`/lab/event-actions/${id}`, json("DELETE")),
  createScenarioEvent: (scenario: string, body: Row) => request<Row>(`/lab/scenarios/${scenario}/events`, json("POST", body)),
  updateScenarioEvent: (id: string, body: Row) => request<Row>(`/lab/scenario-events/${id}`, json("PATCH", body)),
  deleteScenarioEvent: (id: string) => request<void>(`/lab/scenario-events/${id}`, json("DELETE")),
  pilotQualification: () => request<Row>("/lab/qualification/pilots", undefined, 60_000),
  createCepRule: (scenario: string, body: Row) => request<Row>(`/lab/scenarios/${scenario}/cep-rules`, json("POST", body)),
  validate: (version: string) => request<Row>(`/lab/versions/${version}/validate`, json("POST")),
  publish: (version: string) => request<Row>(`/lab/versions/${version}/publish`, json("POST")),
  clone: (version: string) => request<Row>(`/lab/versions/${version}/clone`, json("POST")),
  testComponent: (id: string, body: Row) => request<Row>(`/lab/components/${id}/test`, json("POST", body)),
  startRun: (body: Row) => request<Row>("/lab/runs", json("POST", body)),
  getRun: (id: string) => request<Row>(`/lab/runs/${id}`),
  seek: (id: string, body: Row) => request<Row>(`/lab/runs/${id}/seek`, json("POST", body)),
  command: (id: string, body: Row) => request<Row>(`/lab/runs/${id}/commands`, json("POST", body)),
  inject: (id: string, body: Row) => request<Row>(`/lab/runs/${id}/events`, json("POST", body)),
  samples: (id: string) => request<Row>(`/lab/runs/${id}/samples`),
  workflows: (id: string) => request<Row>(`/lab/runs/${id}/workflows`),
  completeWorkflowTask: (run: string, token: string, body: Row = {}) => request<Row>(`/lab/runs/${run}/workflow-tasks/${token}/complete`, json("POST", body)),
  userCommand: (id: string, body: Row) => request<Row>(`/lab/runs/${id}/user-commands`, json("POST", body)),
  testScript: (id: string, body: Row) => request<Row>(`/lab/scripts/versions/${id}/test`, json("POST", body)),
};
