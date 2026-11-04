import { useCallback, useEffect, useRef, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, type PortContract, type Row, type Workspace } from "./api";
import { BpmnCollaboration, BpmnEditor } from "./BpmnEditor";
import { processCatalog } from "./testBindings";
import { defaultProcess, hasSystemModel, inPack, readStoredPack, selectablePacks, storePack } from "./packs";
import { ReportsView } from "./ReportsView";
import {
  EVENT_ACTION_LABELS,
  actionTargetKind,
  CREATABLE_EVENT_ACTION_TYPES,
  coerceSchemaValue,
  compatibleEventTargets,
  eventOverlay,
  formatZonedDateTimeLocal,
  orderedActions,
  parseZonedDateTimeLocal,
  payloadDefaults,
  type EventAction,
  type EventActionType,
  type EventProgram,
} from "./eventDesigner";
import {
  diagnosticSeriesDash,
  diagnosticSeriesLaneY,
  diagnosticSeriesStats,
  diagnosticSeriesValue,
  formatDiagnosticValue,
  preferredDiagnosticPoint,
  reconcileDiagnosticSets,
  resolveSeriesNodeId,
} from "./chartNavigation";
import { DEFAULT_SCENARIO_ID, runModelVersionId, shouldAutoStartDefaultScenario } from "./startup";
import { scenarioForModel, selectableModels } from "./modelNavigation";
import { timelineViewport, type TimelineScale } from "./timelineViewport";

const palette: Record<string, string> = {
  resource: "#64d8cb",
  gas: "#77bdfb",
  generation: "#f0bd63",
  substation: "#d899ff",
  line: "#ff8f72",
  grid: "#b7f34a",
  end_product: "#f3f3f0",
  start: "#64d8cb",
  event_trigger: "#ff8f72",
  python_action: "#d899ff",
  resource_operation: "#f0bd63",
  wait: "#77bdfb",
  component_command: "#b7f34a",
  metric_check: "#70d6ff",
  success: "#82e072",
  failure: "#ff786d",
};
const clock = (value?: string) =>
  value
    ? new Intl.DateTimeFormat("uk-UA", {
        timeZone: "Europe/Kyiv",
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value))
    : "—";
const pct = (value: number, min: number, max: number) =>
  Math.max(0, Math.min(100, ((value - min) / Math.max(1, max - min)) * 100));
function GraphNode({ data, selected }: any) {
  const inputs = (data.ports || []).filter((port: PortContract) => port.direction !== "output"),
    outputs = (data.ports || []).filter((port: PortContract) => port.direction !== "input");
  return (
    <div
      className={`model-node ${selected ? "selected" : ""} ${data.online === false ? "offline" : ""}`}
      style={{ "--node": palette[data.category] || "#b7f34a" } as any}
    >
      {data.eventBackground && <Handle className="event-target-handle" type="target" id="event-target" position={Position.Top} />}
      {inputs.map((port: PortContract, index: number) => <Handle className={`typed-port domain-${port.domain.replaceAll(".", "-")}`} title={`${port.name} · ${port.domain}`} type="target" id={port.key} position={Position.Left} style={{ top: `${((index + 1) * 100) / (inputs.length + 1)}%` }} key={`in-${port.key}`} />)}
      <small>
        {data.level || "FLOW"} · {data.category}
      </small>
      <b>{data.label}</b>
      <span>
        {data.online === false
          ? "ВІДКЛЮЧЕНО"
          : data.output || data.eventType || "готовий"}
      </span>
      <div className="node-port-summary">
        {inputs.map((port: PortContract) => <em title={`${port.domain} · ${port.quantity}`} key={`summary-in-${port.key}`}>→ {port.name}</em>)}
        {outputs.map((port: PortContract) => <em title={`${port.domain} · ${port.quantity}`} key={`summary-out-${port.key}`}>{port.name} →</em>)}
      </div>
      <div className="node-domains">{[...new Set((data.ports || []).map((port: PortContract) => port.domain))].map((domain: any) => <em key={domain}>{domain}</em>)}</div>
      {outputs.map((port: PortContract, index: number) => <Handle className={`typed-port domain-${port.domain.replaceAll(".", "-")}`} title={`${port.name} · ${port.domain}`} type="source" id={port.key} position={Position.Right} style={{ top: `${((index + 1) * 100) / (outputs.length + 1)}%` }} key={`out-${port.key}`} />)}
    </div>
  );
}
const bpmnSymbols: Record<string, string> = {
  event_trigger: "✉",
  timer: "◷",
  condition: "×",
  parallel_split: "+",
  join: "+",
  python_action: "≡",
  resource_operation: "▤",
  component_command: "⚙",
  metric_check: "◇",
  manual_command: "✋",
};
function BpmnNode({ data, selected }: any) {
  const kind = String(data.category || "task"),
    isStart = kind === "start",
    isEnd = kind === "success" || kind === "failure",
    isGateway = ["condition", "parallel_split", "join"].includes(kind),
    isIntermediate = ["event_trigger", "timer", "wait"].includes(kind),
    family = isStart || isEnd || isIntermediate ? "event" : isGateway ? "gateway" : "task";
  return <div className={`bpmn-node bpmn-${family} bpmn-kind-${kind} ${selected ? "selected" : ""}`}>
    {!isStart && <Handle className="bpmn-handle" type="target" id="in" position={Position.Left} />}
    <div className="bpmn-shape" aria-label={`${kind}: ${data.label}`}>
      {family === "task" && <small>{bpmnSymbols[kind] || ""}</small>}
      {family !== "task" && <strong>{isEnd ? "" : bpmnSymbols[kind] || ""}</strong>}
      {family === "task" && <b>{data.label}</b>}
    </div>
    {family !== "task" && <div className="bpmn-label"><b>{data.label}</b><span>{data.eventType || kind}</span></div>}
    {family === "task" && data.eventType && <span className="bpmn-event-type">{data.eventType}</span>}
    {!isEnd && <Handle className="bpmn-handle" type="source" id="out" position={Position.Right} />}
  </div>;
}
function EventTriggerNode({ data, selected }: any) {
  return <div className={`event-trigger-node ${selected ? "selected" : ""} ${data.enabled ? "" : "disabled"}`}><small>ТРИГЕР ПОДІЇ</small><b>{data.label}</b><span>{data.eventType}</span><Handle type="source" id="next" position={Position.Right} /></div>;
}
function EventActionNode({ data, selected }: any) {
  return <div className={`event-action-node ${selected ? "selected" : ""}`}><Handle type="target" id="previous" position={Position.Left} /><small>ДІЯ {data.sequence}</small><b>{data.label}</b><span>{data.targetId ? "ціль підключено" : data.actionType === "event.emit" ? "системна дія" : "оберіть ціль"}</span><Handle type="source" id="next" position={Position.Right} /><Handle className="event-action-target-handle" type="source" id="target-link" position={Position.Bottom} /></div>;
}
function EventConnectionNode({ data, selected }: any) {
  return <div className={`event-connection-node ${selected ? "selected" : ""}`}><Handle type="target" id="event-target" position={Position.Top} /><small>ЗВ’ЯЗОК</small><b>{data.label}</b></div>;
}
const nodeTypes = { lab: GraphNode, workflow: BpmnNode, eventTrigger: EventTriggerNode, eventAction: EventActionNode, eventConnection: EventConnectionNode };

export default function App() {
  const [w, setW] = useState<Workspace | null>(null),
    [projectId, setProjectId] = useState(""),
    [reportLaunchId, setReportLaunchId] = useState<string | null>(null),
    [packs, setPacks] = useState<Row[]>([]),
    [packId, setPackId] = useState<string>(() => readStoredPack() ?? ""),
    [siteSettings, setSiteSettings] = useState<Row | null>(null),
    [replayRequest, setReplayRequest] = useState<{ runId: string; nonce: number } | null>(null),
    [mode, setMode] = useState<"model" | "process" | "events" | "participants" | "reports">("model"),
    [viewVersion, setViewVersion] = useState<Row | null>(null),
    [graphRows, setGraphRows] = useState<Row[]>([]),
    [edgeRows, setEdgeRows] = useState<Row[]>([]),
    [observables, setObservables] = useState<any[]>([]),
    [workflow, setWorkflow] = useState<Row | null>(null),
    [eventPrograms, setEventPrograms] = useState<EventProgram[]>([]),
    [selectedProgramId, setSelectedProgramId] = useState<string | null>(null),
    [selectedActionId, setSelectedActionId] = useState<string | null>(null),
    [eventPanelTab, setEventPanelTab] = useState<"program" | "calendar" | "qualification" | "journal">("program"),
    [pilotGate, setPilotGate] = useState<Row | null>(null),
    [qualificationDefinition, setQualificationDefinition] = useState<Row | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [selectedId, setSelectedId] = useState<string | null>(null),
    [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null),
    [sideTab, setSideTab] = useState("element"),
    [detailTab, setDetailTab] = useState("parameters"),
    [run, setRun] = useState<Row | null>(null),
    [test, setTest] = useState<Row | null>(null),
    [validation, setValidation] = useState<Row | null>(null),
    [scenario, setScenario] = useState(DEFAULT_SCENARIO_ID),
    [timeline, setTimeline] = useState<Row | null>(null),
    [assembly, setAssembly] = useState<Row | null>(null),
    [flow, setFlow] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]),
    [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const autoStartRequested = useRef(false);
  const modelNodes = useCallback(
    (rows: Row[], components: Row[]) =>
      rows.map((n) => {
        const c = components.find((x) => x.id === n.component_type_id);
        return {
          id: n.id,
          type: "lab",
          position: { x: n.x, y: n.y },
          data: {
            label: n.name,
            category: c?.category,
            level: c?.level,
            online: true,
            ports: c?.ports || [],
          },
        };
      }),
    [],
  );
  const modelEdges = (rows: Row[]) =>
    rows.map((e) => ({
      id: e.id,
      source: e.source_node_id,
      target: e.target_node_id,
      sourceHandle: e.source_port,
      targetHandle: e.target_port,
      label: e.flow_type,
      animated: e.enabled !== false,
      className: `domain-edge domain-${String(e.domain || e.flow_type).replaceAll(".", "-")}`,
      data: e,
    }));
  const workflowNodes = (rows: Row[]) =>
    rows.map((n) => ({
      id: n.id,
      type: "workflow",
      position: { x: n.x, y: n.y },
      data: {
        label: n.name,
        category: n.block_type,
        level: n.block_type,
        eventType: n.config?.event_type,
      },
    }));
  const workflowEdges = (rows: Row[]) =>
    rows.map((e) => ({
      id: e.id,
      source: e.source_node_id,
      target: e.target_node_id,
      label: e.condition_code || "True",
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#d8e1dd" },
      className: "bpmn-sequence-flow",
    }));
  const refresh = useCallback(async () => {
    try {
      const next = await api.workspace(projectId || undefined);
      setW(next);
      api.packs().then(setPacks).catch(() => setPacks([]));
      setProjectId(next.project.id);
      setViewVersion(next.version);
      setGraphRows(next.nodes);
      setEdgeRows(next.edges);
      setEventPrograms(next.event_programs || []);
      setSelectedProgramId((current) => current && (next.event_programs || []).some((item) => item.id === current) ? current : next.event_programs?.[0]?.id || null);
      setObservables(next.observables || []);
      setNodes(modelNodes(next.nodes, next.components));
      setEdges(modelEdges(next.edges));
      const qualification = await api.qualificationDefinition(next.version.id);
      setQualificationDefinition(qualification.item);
    } catch (e) {
      setError(String(e));
    }
  }, [setNodes, setEdges, modelNodes, projectId]);
  useEffect(() => {
    refresh();
  }, [refresh]);
  useEffect(() => {
    if (!w) return;
    const item = w.scenarios.find((x) => x.id === scenario);
    setRun(null);
    setSelectedId(null);
    api
      .scenarioTimeline(scenario)
      .then(setTimeline)
      .catch((e) => setError(String(e)));
    api
      .scenarioAssembly(scenario)
      .then(setAssembly)
      .catch((e) => setError(String(e)));
    if (!item?.model_version_id) return;
    api
      .graph(scenario === "baseline" ? w.version.id : item.model_version_id)
      .then((graph) => {
        setViewVersion(graph.version);
        setGraphRows(graph.nodes);
        setEdgeRows(graph.edges);
        setEventPrograms(graph.event_programs || []);
        setSelectedProgramId((current) => current && (graph.event_programs || []).some((item: EventProgram) => item.id === current) ? current : graph.event_programs?.[0]?.id || null);
        setObservables(graph.observables || []);
        setNodes(modelNodes(graph.nodes, w.components));
        setEdges(modelEdges(graph.edges));
        api.qualificationDefinition(graph.version.id).then((result) => setQualificationDefinition(result.item)).catch((e) => setError(String(e)));
      })
      .catch((e) => setError(String(e)));
    const workflowId = item.configuration?.workflow_version_id;
    if (workflowId)
      api
        .workflow(workflowId)
        .then(setWorkflow)
        .catch((e) => setError(String(e)));
    else setWorkflow(null);
  }, [scenario, w, setNodes, setEdges, modelNodes]);
  useEffect(() => {
    if (mode === "process") {
      if (workflow) {
        setNodes(workflowNodes(workflow.nodes));
        setEdges(workflowEdges(workflow.transitions));
      } else {
        setNodes([]);
        setEdges([]);
      }
    } else if (mode === "events" && w) {
      const program = eventPrograms.find((item) => item.id === selectedProgramId);
      const baseNodes = modelNodes(graphRows, w.components);
      const baseEdges = modelEdges(edgeRows);
      if (program) {
        const overlay = eventOverlay({ program, modelNodes: baseNodes, modelEdges: baseEdges, graphRows, edgeRows });
        setNodes(overlay.nodes);
        setEdges(overlay.edges);
      } else {
        setNodes(baseNodes.map((node) => ({ ...node, draggable: false, className: "event-model-background", data: { ...node.data, eventBackground: true } })));
        setEdges(baseEdges.map((edge) => ({ ...edge, animated: false, className: `${edge.className || ""} event-model-edge` })));
      }
    } else if (w) {
      setNodes(modelNodes(graphRows, w.components));
      setEdges(modelEdges(edgeRows));
    }
  }, [
    mode,
    workflow,
    w,
    graphRows,
    edgeRows,
    eventPrograms,
    selectedProgramId,
    setNodes,
    setEdges,
    modelNodes,
  ]);
  useEffect(() => {
    if (!run?.id || ["completed", "failed", "stopped"].includes(run.status))
      return;
    const id = setInterval(
      () =>
        api
          .getRun(run.id)
          .then(setRun)
          .catch(() => {}),
      650,
    );
    return () => clearInterval(id);
  }, [run?.id, run?.status]);
  useEffect(() => {
    if (mode !== "events" || pilotGate) return;
    api.pilotQualification().then(setPilotGate).catch((e) => setError(String(e)));
  }, [mode, pilotGate]);
  useEffect(() => {
    if (mode !== "model" || !run?.state?.nodes || !w) return;
    setNodes((old) =>
      old.map((n) => {
        const type = graphRows.find(
          (item) => item.id === n.id,
        )?.component_type_id;
        const current =
          run.state.nodes[n.id] ||
          Object.entries(run.state.nodes).find(([id]) =>
            id.endsWith(`-${type}`),
          )?.[1] ||
          {};
        return {
          ...n,
          data: {
            ...n.data,
            online: (current as any).state?.online,
            output: Object.entries((current as any).outputs || {})
              .slice(0, 2)
              .map(([k, v]) => `${k} ${Number(v).toFixed(1)}`)
              .join(" · "),
          },
        };
      }),
    );
  }, [run?.state, w, graphRows, mode, setNodes]);
  const selectedProgram = eventPrograms.find((item) => item.id === selectedProgramId) || null,
    selectedAction = selectedProgram?.actions?.find((item) => item.id === selectedActionId) || null,
    selected =
      mode === "process"
        ? workflow?.nodes?.find((n: Row) => n.id === selectedId)
        : graphRows.find((n) => n.id === selectedId),
    component = w?.components.find((c) => c.id === selected?.component_type_id),
    selectedEdge = mode === "process"
      ? workflow?.transitions?.find((edge: Row) => edge.id === selectedEdgeId)
      : edgeRows.find((edge) => edge.id === selectedEdgeId),
    immutable =
      mode === "process"
        ? workflow?.version?.status !== "draft"
        : mode === "model" || mode === "events"
        ? viewVersion?.status !== "draft"
        : true;
  const mutate = async (task: () => Promise<any>, refreshAfter = true) => {
    setBusy(true);
    setError("");
    setValidation(null);
    try {
      await task();
      if (refreshAfter) {
        if (mode === "process" && workflow)
          setWorkflow(await api.workflow(workflow.version.id));
        else await refresh();
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const connect = (c: Connection) => {
    if (!c.source || !c.target || !w || !viewVersion) return;
    if (mode === "events") {
      const actionId = c.source.startsWith("event-action:") ? c.source.slice("event-action:".length) : "";
      const action = selectedProgram?.actions?.find((item) => item.id === actionId);
      if (!action) return;
      const connection = c.target.startsWith("event-connection:") ? c.target.slice("event-connection:".length) : null;
      const expectedTarget = actionTargetKind(action.action_type);
      if (c.sourceHandle !== "target-link" || c.targetHandle !== "event-target") return;
      if (expectedTarget === "connection" && !connection) return;
      if (expectedTarget === "component" && (connection || !compatibleEventTargets(action, graphRows, edgeRows, w.components).some((item) => item.id === c.target))) return;
      const body: Row = {
        target_kind: connection ? "connection" : "component",
        target_id: connection || c.target,
      };
      if (action.action_type === "component.patch_state" && !connection) {
        const target = eventTargetsForCurrent(action, graphRows, edgeRows, w.components).find((item) => item.id === c.target), key = target && Object.keys(target.state)[0];
        body.config = { patch: key && target ? { [key]: target.state[key] } : {} };
      }
      mutate(() => api.updateEventAction(action.id, body));
    } else if (mode === "model")
      mutate(() =>
        api.addEdge(viewVersion.id, {
          source_node_id: c.source,
          source_port: c.sourceHandle || "out",
          target_node_id: c.target,
          target_port: c.targetHandle || "in",
        }),
      );
    else if (mode === "process" && workflow)
      mutate(() =>
        api.addWorkflowTransition(workflow.version.id, {
          source_node_id: c.source,
          target_node_id: c.target,
          condition_code: "True",
        }),
      );
  };
  const validConnection = useCallback((connection: Connection | Edge) => {
    if (mode === "events") {
      const actionId = connection.source.startsWith("event-action:") ? connection.source.slice("event-action:".length) : "",
        action = selectedProgram?.actions?.find((item) => item.id === actionId),
        targetKind = action && actionTargetKind(action.action_type);
      if (!action || connection.sourceHandle !== "target-link" || connection.targetHandle !== "event-target") return false;
      if (targetKind === "connection") return connection.target.startsWith("event-connection:");
      if (targetKind === "component") return Boolean(w && compatibleEventTargets(action, graphRows, edgeRows, w.components).some((item) => item.id === connection.target));
      return false;
    }
    const sourceNode = nodes.find((node) => node.id === connection.source), targetNode = nodes.find((node) => node.id === connection.target);
    const source = ((sourceNode?.data as any)?.ports || []).find((port: PortContract) => port.key === connection.sourceHandle),
      target = ((targetNode?.data as any)?.ports || []).find((port: PortContract) => port.key === connection.targetHandle);
    return Boolean(source && target && source.direction !== "input" && target.direction !== "output" && source.domain === target.domain && source.quantity === target.quantity);
  }, [nodes, mode, graphRows, edgeRows, selectedProgram, w]);
  const drop = async (e: React.DragEvent) => {
    e.preventDefault();
    if (immutable || !w) return;
    const bounds = (e.currentTarget as HTMLElement).getBoundingClientRect(),
      position = {
        x: e.clientX - bounds.left - 90,
        y: e.clientY - bounds.top - 35,
      };
    try {
      if (mode === "model") {
        const componentId = e.dataTransfer.getData("model");
        if (!componentId) return;
        await api.addNode(viewVersion?.id || w.version.id, {
          component_type_id: componentId,
          name:
            w.components.find((c) => c.id === componentId)?.name ||
            "Новий елемент",
          x: position.x,
          y: position.y,
        });
      } else if (mode === "process" && workflow) {
        const blockType = e.dataTransfer.getData("process");
        if (!blockType) return;
        await api.addWorkflowNode(workflow.version.id, {
          name: blockType.replaceAll("_", " "),
          block_type: blockType,
          x: position.x,
          y: position.y,
          config: {},
        });
      }
      await refresh();
      if (mode === "process" && workflow)
        setWorkflow(await api.workflow(workflow.version.id));
    } catch (err) {
      setError(String(err));
    }
  };
  const runModel = async (settings: Row = {}) => {
    if (!w) return;
    try {
      setBusy(true);
      setError("");
      const selectedScenario = w.scenarios.find((item) => item.id === scenario);
      const started = await api.startRun({
        model_version_id: runModelVersionId(
          selectedScenario?.model_version_id,
          w.project.published_version_id,
        ),
        scenario_id: scenario,
        ...settings,
      });
      setRun(await api.getRun(started.id));
      setSideTab("events");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  useEffect(() => {
    const selectedScenario = w?.scenarios.find((item) => item.id === scenario);
    if (!shouldAutoStartDefaultScenario({
      scenarioId: scenario,
      scenarioModelVersionId: selectedScenario?.model_version_id,
      loadedModelVersionId: viewVersion?.id,
      timelineReady: Boolean(timeline),
      runExists: Boolean(run),
      alreadyRequested: autoStartRequested.current,
    })) return;
    autoStartRequested.current = true;
    void runModel();
  }, [scenario, timeline, viewVersion?.id, w, run]);
  const focusGraphNode = useCallback((nodeId: string) => {
    if (!graphRows.some((node) => node.id === nodeId)) return;
    setMode("model");
    setSelectedId(nodeId);
    setSelectedEdgeId(null);
    setSideTab("element");
    setDetailTab("monitoring");
    window.setTimeout(() => {
      flow?.fitView({ nodes: [{ id: nodeId }], padding: 1.5, duration: 550 });
      document.querySelector(".canvas")?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 0);
  }, [flow, graphRows]);
  const currentPack = selectablePacks(packs).find((pack) => pack.id === packId);
  const processOnly = !!currentPack && !hasSystemModel(currentPack);
  useEffect(() => {
    // Without a choice made in this tab, open the contour named in the settings.
    api.settings().then((settings) => {
      setSiteSettings(settings);
      if (readStoredPack() === null && settings.default_pack) setPackId(settings.default_pack);
    }).catch(() => setSiteSettings({}));
  }, []);
  useEffect(() => {
    // A contour of processes opens straight on its default process.
    if (!processOnly || mode !== "process" || workflow || !w) return;
    const workflowId = defaultProcess(currentPack, siteSettings?.default_process || "");
    const version = processCatalog(w.workflows).find((item) => item.workflow_id === workflowId);
    if (version) api.workflow(version.id).then(setWorkflow).catch((error) => setError(String(error)));
  }, [processOnly, mode, workflow, w, currentPack, siteSettings]);
  useEffect(() => {
    // A pack with processes only (no system model) opens straight in BPMN.
    if (currentPack && !hasSystemModel(currentPack) && (mode === "model" || mode === "events" || mode === "participants")) setMode("process");
  }, [currentPack, mode]);
  if (!w)
    return <div className="loading">MODEL LAB · завантаження… {error}</div>;
  return (
    <main className={`app-shell ${mode === "reports" || processOnly ? "reports-shell" : ""}`}>
      <header className="topbar">
        <div className="brand">
          <i />
          <span>
            <b>AFTERTIME</b>
            <small>MODEL LAB · SCRIPTED RUNTIME</small>
          </span>
        </div>
        <div className="title">
          <small>ВИКОНУВАНА МОДЕЛЬ · БЕЗ ТЕРИТОРІЙ</small>
          <b>
            {mode === "process"
              ? workflow?.version?.name || "Process Designer"
              : mode === "participants"
                ? assembly?.assembly?.name || "Учасники та процеси"
              : mode === "events"
                ? selectedProgram?.name || "Редактор подій"
                : viewVersion?.name || w.project.name}
          </b>
        </div>
        <div className="model-tools">
          <select aria-label="Пакет моделі" className="pack-picker" value={currentPack?.id || ""} onChange={(event) => {
            const next = packs.find((pack) => pack.id === event.target.value);
            setPackId(event.target.value);
            storePack(event.target.value);
            setWorkflow(null);
            if (!hasSystemModel(next)) setMode("process");
            else if (next && !next.projects.includes(w.project.id)) setProjectId(next.projects[0]);
          }}>
            <option value="">Усі моделі</option>
            {selectablePacks(packs).map((pack) => <option value={pack.id} key={pack.id}>{pack.title}</option>)}
          </select>
          {hasSystemModel(currentPack) && <select aria-label="Проєкт" value={w.project.id} onChange={(event) => { setProjectId(event.target.value); setSelectedId(null); setSelectedEdgeId(null); }}>
            {inPack(w.projects || [w.project], currentPack, "projects", (project: Row) => project.id).map((project: Row) => <option value={project.id} key={project.id}>{project.name}</option>)}
          </select>}
          <select aria-label="Модель" value={viewVersion?.id || w.version.id} onChange={async (event) => {
            const modelVersionId = event.target.value;
            const linkedScenario = scenarioForModel(w.scenarios, modelVersionId);
            setRun(null);
            setSelectedId(null);
            setSelectedEdgeId(null);
            if (linkedScenario && linkedScenario.id !== scenario) {
              setScenario(linkedScenario.id);
              return;
            }
            const graph = await api.graph(modelVersionId);
            setViewVersion(graph.version);
            setGraphRows(graph.nodes);
             setEdgeRows(graph.edges);
             setEventPrograms(graph.event_programs || []);
             setSelectedProgramId(graph.event_programs?.[0]?.id || null);
             setObservables(graph.observables || []);
            setNodes(modelNodes(graph.nodes, w.components));
            setEdges(modelEdges(graph.edges));
            const qualification = await api.qualificationDefinition(graph.version.id);
            setQualificationDefinition(qualification.item);
          }}>
            {selectableModels(w.models || [w.version]).map((model: Row) => (
              <option value={model.id} key={model.id}>{model.name} · v{model.version} · {model.status}</option>
            ))}
          </select>
          <button onClick={async () => {
            const name = window.prompt("Назва нової моделі");
            if (!name) return;
            const created = await api.createProject({ name, version_name: name, pack_id: currentPack?.id });
            setProjectId(created.project.id);
          }}>＋ Нова модель</button>
        </div>
        <div className="mode-switch">
          <button
            className={mode === "model" ? "active" : ""}
            disabled={!hasSystemModel(currentPack)}
            onClick={() => {
              setMode("model");
              setSelectedId(null);
            }}
          >
            Модель
          </button>
          <button
            className={mode === "process" ? "active" : ""}
            onClick={() => {
              setMode("process");
              setSelectedId(null);
            }}
          >
            BPMN 2.0
          </button>
          <button
            className={mode === "events" ? "active" : ""}
            disabled={!hasSystemModel(currentPack)}
            onClick={() => {
              setMode("events");
              setSelectedId(null);
              setSelectedEdgeId(null);
              setSelectedActionId(null);
              setEventPanelTab("program");
            }}
          >
            Події
          </button>
          <button
            className={mode === "participants" ? "active" : ""}
            disabled={!hasSystemModel(currentPack)}
            onClick={() => {
              setMode("participants");
              setSelectedId(null);
              setSelectedEdgeId(null);
            }}
          >
            Учасники та процеси
          </button>
          <button
            className={mode === "reports" ? "active" : ""}
            onClick={() => {
              setReportLaunchId(null);
              setMode("reports");
            }}
          >
            Звіти
          </button>
        </div>
        <div className="version">
          <span
            className={
              mode === "process"
                ? workflow?.version?.status
                : mode === "participants"
                  ? assembly?.assembly?.status
                : viewVersion?.status
            }
          >
            {mode === "process"
              ? workflow?.version?.status || "—"
              : mode === "participants"
                ? assembly?.assembly?.status || "—"
              : viewVersion?.status}
          </span>
          <b>
            v
            {mode === "process"
              ? workflow?.version?.version || "—"
              : mode === "participants"
                ? assembly?.assembly?.version || "—"
              : viewVersion?.version}
          </b>
        </div>
      </header>
      {error && (
        <div className="error">
          {error}
          <button onClick={() => setError("")}>×</button>
        </div>
      )}
      <section className={`workbench ${mode === "participants" || mode === "reports" ? "participant-workbench" : ""} ${processOnly && mode === "process" ? "process-only" : ""}`}>
        {mode === "reports" ? <ReportsView
          initialLaunchId={reportLaunchId}
          onError={setError}
          onOpenProcess={async (binding, replayRunId) => {
            const target = processCatalog(w?.workflows || []).find((item) =>
              item.bpmn_xml?.includes(`aftertime:testBinding="${binding}"`));
            if (!target) { setError(`Процес із прив'язкою «${binding}» не знайдено`); return; }
            if (workflow?.version?.id !== target.id) setWorkflow(await api.workflow(target.id));
            setValidation(null);
            setReplayRequest(replayRunId ? { runId: replayRunId, nonce: Date.now() } : null);
            setMode("process");
          }}
        /> : mode === "participants" ? <ParticipantProcessTable
          data={assembly}
          onOpenWorkflow={async (versionId) => {
            setWorkflow(await api.workflow(versionId));
            setMode("process");
            setSelectedId(null);
            setSideTab("process");
          }}
          onCloneAssembly={async () => {
            if (!assembly?.assembly?.id) return;
            setAssembly(await api.cloneAssembly(assembly.assembly.id));
          }}
        /> : <>
        {mode === "events" ? <EventProgramPalette w={w} versionId={viewVersion?.id || w.version.id} programs={eventPrograms} selectedId={selectedProgramId} select={(id) => { setSelectedProgramId(id); setSelectedActionId(null); setEventPanelTab("program"); }} disabled={immutable} mutate={mutate} /> : <Palette mode={mode} w={w} disabled={immutable} />}
        <section
          className={`canvas ${mode === "process" ? "bpmn-canvas" : ""}`}
          onDragOver={(e) => e.preventDefault()}
          onDrop={drop}
        >
          <div className="toolbar">
            {mode !== "process" ? (
              <>
                {immutable && viewVersion && <button onClick={() => mutate(async () => {
                  const draft = await api.clone(viewVersion.id), graph = await api.graph(draft.id), qualification = await api.qualificationDefinition(draft.id);
                  setViewVersion(graph.version);
                  setGraphRows(graph.nodes);
                  setEdgeRows(graph.edges);
                  setEventPrograms(graph.event_programs || []);
                  setSelectedProgramId(graph.event_programs?.[0]?.id || null);
                  setObservables(graph.observables || []);
                  setQualificationDefinition(qualification.item);
                  setNodes(modelNodes(graph.nodes, w.components));
                  setEdges(modelEdges(graph.edges));
                }, false)}>Створити draft-копію</button>}
                <button
                  disabled={immutable}
                  onClick={() =>
                    mutate(async () =>
                      setValidation(await api.validate(viewVersion?.id || w.version.id)),
                    )
                  }
                >
                  ✓ Перевірити
                </button>
                <button
                  disabled={immutable || !validation?.valid}
                  onClick={() => mutate(() => api.publish(viewVersion?.id || w.version.id))}
                >
                  Опублікувати
                </button>
              </>
            ) : (
              <>
                {immutable && workflow && (
                  <button
                    onClick={async () => {
                      const draft = await api.cloneWorkflow(
                        workflow.version.id,
                      );
                      setWorkflow(draft);
                      setValidation(null);
                    }}
                  >
                    Створити draft-копію
                  </button>
                )}
                <button
                  disabled={immutable || !workflow}
                  onClick={async () =>
                    workflow &&
                    setValidation(
                      await api.validateWorkflow(workflow.version.id),
                    )
                  }
                >
                  ✓ Перевірити workflow
                </button>
                <button
                  disabled={immutable || !workflow || !validation?.valid}
                  onClick={async () => {
                    if (!workflow) return;
                    await api.publishWorkflow(workflow.version.id);
                    setWorkflow(await api.workflow(workflow.version.id));
                  }}
                >
                  Опублікувати workflow
                </button>
              </>
            )}
            {mode === "process" && w && (
              <select
                className="process-picker"
                value={workflow?.version?.id || ""}
                onChange={async (event) => {
                  if (!event.target.value) return;
                  setWorkflow(await api.workflow(event.target.value));
                  setValidation(null);
                }}
              >
                <option value="">Оберіть процес…</option>
                {processCatalog(inPack(w.workflows, currentPack, "workflows", (row: Row) => row.workflow_id)).map((item) => (
                  <option value={item.id} key={item.id}>
                    {item.tested ? "✓ E2E · " : ""}{item.name} · v{item.version}
                  </option>
                ))}
              </select>
            )}
            <span>{mode === "process" ? "BPMN 2.0 · " : ""}{immutable ? "Immutable snapshot · " : "Draft · "}{mode === "events" ? `${eventPrograms.length} програм · ${selectedProgram?.actions?.length || 0} дій` : `${nodes.length} вузлів · ${edges.length} зв’язків`}</span>
          </div>
          {mode === "process" && workflow ? <BpmnEditor
            workflow={workflow}
            immutable={immutable}
            onSaved={setWorkflow}
            onValidation={setValidation}
            onError={setError}
            onOpenReport={(launchId) => {
              setReportLaunchId(launchId);
              setMode("reports");
            }}
            replayRequest={replayRequest}
          /> : <ReactFlow
            onInit={setFlow}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            nodesDraggable={!immutable}
            nodesConnectable={!immutable}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={connect}
            isValidConnection={mode !== "process" ? validConnection : undefined}
            onNodeClick={(_, n) => {
              if (mode === "events") {
                if (n.id.startsWith("event-program:")) {
                  setSelectedProgramId(n.id.slice("event-program:".length));
                  setSelectedActionId(null);
                } else if (n.id.startsWith("event-action:")) {
                  setSelectedActionId(n.id.slice("event-action:".length));
                } else if (n.id.startsWith("event-connection:")) {
                  setSelectedEdgeId(n.id.slice("event-connection:".length));
                } else {
                  setSelectedId(n.id);
                }
                setEventPanelTab("program");
                return;
              }
              setSelectedId(n.id);
              setSelectedEdgeId(null);
              setSideTab("element");
            }}
            onEdgeClick={(_, edge) => {
              if (mode === "events") {
                const modelEdge = edgeRows.find((item) => item.id === edge.id);
                if (modelEdge && selectedAction && actionTargetKind(selectedAction.action_type) === "connection" && !immutable) {
                  mutate(() => api.updateEventAction(selectedAction.id, { target_kind: "connection", target_id: modelEdge.id }));
                }
                setSelectedEdgeId(modelEdge?.id || null);
                return;
              }
              if (mode === "process") {
                setSelectedEdgeId(edge.id);
                setSelectedId(null);
                setSideTab("element");
                return;
              }
              if (mode !== "model") return;
              setSelectedEdgeId(edge.id);
              setSelectedId(null);
              setSideTab("element");
            }}
            onNodeDragStop={(_, n) => {
              if (immutable) return;
              if (mode === "events") {
                if (n.id.startsWith("event-program:") && selectedProgram) {
                  api.updateEventProgram(selectedProgram.id, { layout: { ...selectedProgram.layout, trigger: n.position } });
                } else if (n.id.startsWith("event-action:")) {
                  api.updateEventAction(n.id.slice("event-action:".length), { x: n.position.x, y: n.position.y });
                }
              } else if (mode === "model")
                api.updateNode(n.id, { x: n.position.x, y: n.position.y });
              else
                api.updateWorkflowNode(n.id, {
                  x: n.position.x,
                  y: n.position.y,
                });
            }}
            fitView
          >
            <Background gap={20} color="#17332b" />
            <MiniMap
              pannable
              zoomable
              nodeColor={(n) => palette[(n.data as any).category]}
            />
            <Controls />
          </ReactFlow>}
        </section>
        {mode === "events" ? <EventDesignerPanel
          w={w}
          program={selectedProgram}
          action={selectedAction}
          programs={eventPrograms}
          graphRows={graphRows}
          edgeRows={edgeRows}
          timeline={timeline}
          timezone={viewVersion?.timezone || w.version.timezone || "Europe/Kyiv"}
          run={run}
          pilotGate={pilotGate}
          qualificationDefinition={qualificationDefinition}
          viewVersion={viewVersion}
          scenario={scenario}
          tab={eventPanelTab}
          setTab={setEventPanelTab}
          immutable={immutable}
          selectAction={setSelectedActionId}
          mutate={mutate}
          reloadTimeline={async () => setTimeline(await api.scenarioTimeline(scenario))}
          reloadQualification={async () => setPilotGate(await api.pilotQualification())}
          reloadDefinition={async () => viewVersion && setQualificationDefinition((await api.qualificationDefinition(viewVersion.id)).item)}
        /> : <RightPanel
          run={run}
          timeline={timeline}
          selected={selected}
          selectedEdge={selectedEdge}
          component={component}
          w={{ ...w, observables }}
          viewVersion={viewVersion}
          scenario={scenario}
          workflow={workflow}
          sideTab={sideTab}
          setSideTab={setSideTab}
          detailTab={detailTab}
          setDetailTab={setDetailTab}
          immutable={immutable}
          test={test}
          setTest={setTest}
          mutate={mutate}
          mode={mode}
        />}
        </>}
      </section>
      {mode !== "reports" && !processOnly && <Monitor
        w={{ ...w, observables }}
        graphRows={graphRows}
        run={run}
        timeline={timeline}
        scenario={scenario}
        setScenario={setScenario}
        start={runModel}
        setRun={setRun}
        selectedNodeId={selectedId}
        focusNode={focusGraphNode}
      />}
      {busy && <div className="busy">ОБРОБКА…</div>}
    </main>
  );
}

function ParticipantProcessTable({ data, onOpenWorkflow, onCloneAssembly }: { data: Row | null; onOpenWorkflow: (versionId: string) => Promise<void>; onCloneAssembly: () => Promise<void> }) {
  const [query, setQuery] = useState("");
  const rows: Row[] = data?.objects || [];
  const filtered = rows.filter((item) => {
    const names = (item.process_sets || []).flatMap((set: Row) => (set.workflows || []).map((workflow: Row) => workflow.workflow_name)).join(" ");
    return `${item.organization} ${item.participant_role} ${item.asset?.name || ""} ${item.asset?.component_type_id || ""} ${names}`.toLowerCase().includes(query.toLowerCase());
  });
  if (!data?.assembly) return <section className="participant-catalog empty"><div><small>СКЛАД СЦЕНАРІЮ</small><h2>Склад учасників ще не визначено</h2><p>Сценарій має часову шкалу, але поки використовує спільну модель і legacy-процес.</p></div></section>;
  return <section className="participant-catalog">
    <header>
      <div><small>СКЛАД СЦЕНАРІЮ</small><h2>{data.assembly.name}</h2><p>Сценарій визначає події. Кожен об’єкт належить учаснику, а реакція запускається з його версійного набору процесів.</p></div>
      <div className="participant-actions"><span className={data.assembly.status}>{data.assembly.status} · v{data.assembly.version}</span>{data.assembly.status === "published" && <button onClick={onCloneAssembly}>Створити чернетку складу</button>}</div>
    </header>
    {data.collaboration_bpmn_xml && <BpmnCollaboration xml={data.collaboration_bpmn_xml} />}
    <div className="participant-summary">
      <span><b>{data.participants?.length || 0}</b> учасників</span><span><b>{rows.filter((item) => item.asset).length}</b> об’єктів</span><span><b>{new Set(rows.flatMap((item) => (item.process_sets || []).map((set: Row) => set.version.id))).size}</b> наборів процесів</span><span><b>{data.cross_links?.length || 0}</b> міжорганізаційних зв’язків</span>
      <input aria-label="Пошук учасників і процесів" placeholder="Пошук об’єкта, компанії або процесу…" value={query} onChange={(event) => setQuery(event.target.value)} />
    </div>
    <div className="participant-table-wrap"><table className="participant-table"><thead><tr><th>Об’єкт</th><th>Власник / роль</th><th>Модель об’єкта</th><th>Набір процесів</th><th>Автозапуск</th><th>Доказовість</th></tr></thead><tbody>
      {filtered.map((item) => {
        const evidence = item.asset?.roles?.[0]?.evidence || item.process_sets?.[0]?.version?.evidence?.[0] || {};
        return <tr key={`${item.member_id}:${item.asset?.mapping?.id || "organization"}`}>
          <td><b>{item.asset?.name || "Організаційний учасник"}</b><small>{item.asset?.component_type_id || item.participant_alias}</small></td>
          <td><b>{item.organization}</b><small>{item.participant_role}</small></td>
          <td>{item.model_version_id ? <><code>{item.model_version_id}</code><small>окрема опублікована модель</small></> : <small>без фізичних об’єктів</small>}</td>
          <td>{(item.process_sets || []).map((set: Row) => {
            const workflows = Array.from(new Map<string, Row>((set.workflows || []).map((workflow: Row) => [workflow.workflow_version_id, workflow])).values());
            return <div className="process-set-cell" key={set.version.id}><b>{set.process_set.name}</b><small>v{set.version.version} · {set.version.status}</small>{workflows.map((workflow) => <button title="Відкрити BPMN 2.0-схему" key={workflow.workflow_version_id} onClick={() => onOpenWorkflow(workflow.workflow_version_id)}><span className={workflow.workflow_validation?.valid ? "bpmn-valid" : "bpmn-invalid"} aria-hidden="true">{workflow.workflow_validation?.valid ? "✓" : "!"}</span> Відкрити BPMN · {workflow.workflow_name}</button>)}</div>;
          })}</td>
          <td>{(item.process_sets || []).flatMap((set: Row) => set.workflows || []).map((workflow: Row) => <span className="trigger-chip" key={workflow.id}>{workflow.start_event_type}<small>{workflow.scope_kind}{workflow.scope_ref ? ` · ${workflow.scope_ref}` : ""}</small></span>)}</td>
          <td><span className={`evidence-chip ${evidence.kind || "missing"}`}>{evidence.kind === "reported" ? "першоджерело" : evidence.kind === "derived" ? "реконструкція" : "не вказано"}</span>{evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer">відкрити джерело ↗</a>}</td>
        </tr>;
      })}
    </tbody></table></div>
  </section>;
}

function EventProgramPalette({ w, versionId, programs, selectedId, select, disabled, mutate }: { w: Workspace; versionId: string; programs: EventProgram[]; selectedId: string | null; select: (id: string) => void; disabled: boolean; mutate: (task: () => Promise<any>) => Promise<void> }) {
  const [query, setQuery] = useState(""),
    unused = (w.event_types || []).filter((type: Row) => !programs.some((program) => program.event_type_id === type.id)),
    [newType, setNewType] = useState("");
  useEffect(() => {
    if (!unused.some((item: Row) => item.id === newType)) setNewType(unused[0]?.id || "");
  }, [unused.map((item: Row) => item.id).join("|"), newType]);
  const visible = programs.filter((program) => `${program.name} ${program.event_type_id}`.toLowerCase().includes(query.toLowerCase()));
  return <aside className="palette event-program-palette">
    <PaneTitle small="БІБЛІОТЕКА" title="Програми подій" />
    <input aria-label="Пошук програм" placeholder="Пошук…" value={query} onChange={(event) => setQuery(event.target.value)} />
    {!disabled && <div className="event-program-create">
      <select aria-label="Тип нової програми" value={newType} onChange={(event) => setNewType(event.target.value)}>
        {unused.map((type: Row) => <option value={type.id} key={type.id}>{type.name}</option>)}
      </select>
      <button disabled={!newType} onClick={() => {
        const type = w.event_types.find((item: Row) => item.id === newType);
        mutate(async () => {
          const created = await api.createEventProgram(versionId, { event_type_id: newType, name: type?.name || newType, description: "", enabled: true, layout: { trigger: { x: 40, y: 60 } } });
          select(created.id);
        });
      }}>＋ Створити</button>
      {!unused.length && <small>Для кожного типу вже створено програму.</small>}
    </div>}
    <div className="palette-list event-program-list">
      {visible.map((program) => {
        const complete = Boolean(program.actions?.length) && program.actions.every((action) => action.action_type === "event.emit" || Boolean(action.target_id));
        return <button className={selectedId === program.id ? "active" : ""} onClick={() => select(program.id)} key={program.id}>
          <i style={{ background: program.enabled ? "#ff8f72" : "#6f7d78" }} />
          <span><b>{program.name}</b><small>{program.event_type_id} · {program.actions?.length || 0} дій · {complete ? "готово" : "потребує налаштування"}</small></span>
          <em className={complete ? "valid" : "invalid"}>{complete ? "✓" : "!"}</em>
        </button>;
      })}
      {!visible.length && <p>Програм не знайдено.</p>}
    </div>
    <p>{disabled ? "Опублікована версія доступна лише для перегляду." : "Оберіть програму, додайте дії та з’єднайте їх з об’єктами моделі."}</p>
  </aside>;
}

function EventDesignerPanel({ w, program, action, programs, graphRows, edgeRows, timeline, timezone, run, pilotGate, qualificationDefinition, viewVersion, scenario, tab, setTab, immutable, selectAction, mutate, reloadTimeline, reloadQualification, reloadDefinition }: any) {
  const tabs: Array<["program" | "calendar" | "qualification" | "journal", string]> = [["program", "Програма"], ["calendar", "Календар"], ["qualification", "Перевірка"], ["journal", "Журнал"]];
  return <aside className="inspector event-designer-inspector">
    <div className="side-tabs event-designer-tabs">{tabs.map(([id, label]) => <button className={tab === id ? "active" : ""} onClick={() => setTab(id)} key={id}>{label}</button>)}</div>
    {tab === "program" && <div className="event-editor-pane">
      <PaneTitle small="EVENT DESIGNER" title={program?.name || "Оберіть програму"} />
      {!program ? <p>Створіть або оберіть програму події з бібліотеки.</p> : <>
        <EventProgramForm key={program.id} program={program} immutable={immutable} mutate={mutate} />
        <div className="event-actions-heading"><b>Впорядковані дії</b><span>{program.actions?.length || 0}</span></div>
        <div className="event-action-list">{orderedActions(program).map((item) => <button className={action?.id === item.id ? "active" : ""} onClick={() => selectAction(item.id)} key={item.id}><em>{item.sequence}</em><span><b>{EVENT_ACTION_LABELS[item.action_type]}</b><small>{eventActionTargetName(item, graphRows, edgeRows)}</small></span></button>)}</div>
        {!immutable && <EventActionCreator program={program} graphRows={graphRows} edgeRows={edgeRows} components={w.components} eventTypes={w.event_types} mutate={mutate} selectAction={selectAction} />}
        {action && <EventActionForm key={action.id} action={action} program={program} graphRows={graphRows} edgeRows={edgeRows} components={w.components} eventTypes={w.event_types} immutable={immutable} mutate={mutate} selectAction={selectAction} />}
      </>}
    </div>}
    {tab === "calendar" && <EventCalendar programs={programs} eventTypes={w.event_types} timeline={timeline} timezone={timezone} scenario={scenario} immutable={false} mutate={mutate} reload={reloadTimeline} />}
    {tab === "qualification" && <QualificationPanel gate={pilotGate} definition={qualificationDefinition} version={viewVersion} scenario={scenario} immutable={immutable} mutate={mutate} reload={reloadQualification} reloadDefinition={reloadDefinition} />}
    {tab === "journal" && <div className="event-editor-pane"><EventList events={run?.events || []} timeline={timeline} /></div>}
  </aside>;
}

function QualificationPanel({ gate, definition, version, scenario, immutable, mutate, reload, reloadDefinition }: any) {
  const [document, setDocument] = useState<Row | null>(definition?.document ? structuredClone(definition.document) : null),
    [localReport, setLocalReport] = useState<Row | null>(null);
  useEffect(() => {
    setDocument(definition?.document ? structuredClone(definition.document) : null);
    setLocalReport(null);
  }, [definition?.id, definition?.revision]);
  const gateReport = gate?.results?.find((item: Row) => item.scenario_id === (document?.scenario_id || scenario)),
    report = localReport || gateReport;
  const patchObject = (index: number, patch: Row) => setDocument((current) => current ? ({ ...current, objects: current.objects.map((item: Row, itemIndex: number) => itemIndex === index ? { ...item, ...patch } : item) }) : current),
    patchProcess = (index: number, patch: Row) => setDocument((current) => current ? ({ ...current, processes: current.processes.map((item: Row, itemIndex: number) => itemIndex === index ? { ...item, ...patch } : item) }) : current),
    patchSituation = (index: number, patch: Row) => setDocument((current) => current ? ({ ...current, situations: current.situations.map((item: Row, itemIndex: number) => itemIndex === index ? { ...item, ...patch } : item) }) : current);
  const save = () => document && version && mutate(async () => {
    await api.saveQualificationDefinition(version.id, document);
    await reloadDefinition();
  }, false);
  const replay = async () => version && setLocalReport(await api.replayQualificationDefinition(version.id));
  return <div className="event-editor-pane qualification-panel">
    <div className="qualification-heading"><PaneTitle small="L1 · ДО СТАБІЛІЗАЦІЇ" title="Історичне відтворення" /><button onClick={reload}>↻ Перевірити всі</button></div>
    {!gate ? <p>Завантаження результатів…</p> : <div className={`qualification-summary ${gate.passed ? "passed" : "failed"}`}><b>{gate.passed ? "Усі кваліфікаційні події відтворено" : "Є помилки відтворення"}</b><span>{gate.passed_count}/{gate.required_count}</span></div>}
    {!document ? <p className="field-hint">Для цієї версії немає строгого L1-опису. Оберіть модель із кваліфікаційним пакетом.</p> : <>
      <div className="qualification-version"><span>Версійований опис</span><b>rev {definition.revision} · {immutable ? "published" : "draft"}</b></div>
      <QualificationMap document={document} />
      <div className="qualification-editor-actions"><button onClick={replay}>▶ Відтворити цю версію</button>{!immutable && <button onClick={save}>Зберегти зміни</button>}</div>
      {report && <div className={`qualification-summary ${report.passed ? "passed" : "failed"}`}><b>{report.passed ? "Відтворено без помилок" : "Помилка відтворення"}</b><span>{report.checkpoints?.filter((item: Row) => item.passed).length || 0}/{report.checkpoints?.length || 0}</span></div>}
      {!!report?.errors?.length && <div className="error">{report.errors.join(" · ")}</div>}
      <div className="qualification-identity form-stack">
        <label>Назва події<input readOnly={immutable} value={document.name || ""} onChange={(event) => setDocument({ ...document, name: event.target.value })} /></label>
        <label>Політика помилки<select disabled={immutable} value={document.run_policy || "pause"} onChange={(event) => setDocument({ ...document, run_policy: event.target.value })}><option value="pause">Пауза</option><option value="stop">Зупинка</option><option value="continue_failed">Продовжити з помилкою</option></select></label>
      </div>
      <section><div className="qualification-section-heading"><h4>UML · стани та дії об’єктів</h4>{!immutable && <button onClick={() => { const name = window.prompt("Назва нового об’єкта"); if (!name) return; const id = uniqueDefinitionId(name, document.objects); setDocument({ ...document, objects: [...document.objects, { id, name, kind: "system_object", initial_state: "initial", states: ["initial"], capabilities: {} }] }); }}>＋ Об’єкт</button>}</div>{(document.objects || []).map((item: Row, objectIndex: number) => <details className="qualification-edit-card" key={item.id}>
        <summary><span><b>{item.name}</b><small>{item.kind} · {item.states?.length || 0} станів · {Object.keys(item.capabilities || {}).length} дій</small></span></summary>
        <div className="form-stack">
          <label>Назва<input readOnly={immutable} value={item.name || ""} onChange={(event) => patchObject(objectIndex, { name: event.target.value })} /></label>
          <label>Початковий стан<select disabled={immutable} value={item.initial_state} onChange={(event) => patchObject(objectIndex, { initial_state: event.target.value })}>{(item.states || []).map((state: string) => <option value={state} key={state}>{state}</option>)}</select></label>
          <label>Оголошені стани<input readOnly={immutable} value={(item.states || []).join(", ")} onChange={(event) => patchObject(objectIndex, { states: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} /></label>
          <div className="qualification-transitions">{Object.entries(item.capabilities || {}).flatMap(([capability, transitions]: [string, any]) => transitions.map((transition: Row, transitionIndex: number) => <div className="qualification-transition" key={`${capability}-${transitionIndex}`}><b>{capability}</b><select disabled={immutable} value={Array.isArray(transition.from) ? transition.from[0] : transition.from} onChange={(event) => { const capabilities = structuredClone(item.capabilities); capabilities[capability][transitionIndex].from = event.target.value; patchObject(objectIndex, { capabilities }); }}>{item.states.map((state: string) => <option value={state} key={state}>{state}</option>)}</select><span>→</span><select disabled={immutable} value={transition.to} onChange={(event) => { const capabilities = structuredClone(item.capabilities); capabilities[capability][transitionIndex].to = event.target.value; patchObject(objectIndex, { capabilities }); }}>{item.states.map((state: string) => <option value={state} key={state}>{state}</option>)}</select>{!immutable && <button aria-label={`Видалити дію ${capability}`} onClick={() => { const capabilities = structuredClone(item.capabilities); capabilities[capability].splice(transitionIndex, 1); if (!capabilities[capability].length) delete capabilities[capability]; patchObject(objectIndex, { capabilities }); }}>×</button>}</div>))}</div>
          {!immutable && <button onClick={() => { const name = window.prompt("Назва capability / дії"); if (!name) return; const capability = uniqueDefinitionId(name, Object.keys(item.capabilities || {}).map((id) => ({ id }))); patchObject(objectIndex, { capabilities: { ...item.capabilities, [capability]: [{ from: item.initial_state, to: item.initial_state }] } }); }}>＋ Додати дію об’єкта</button>}
        </div>
      </details>)}</section>
      <section><div className="qualification-section-heading"><h4>DMN · ситуації</h4>{!immutable && <button onClick={() => { const name = window.prompt("Назва нової ситуації"); if (!name) return; const id = uniqueDefinitionId(name, document.situations); const object = document.objects[0]; setDocument({ ...document, situations: [...document.situations, { id, all: object ? [{ object: object.id, state: object.initial_state }] : [{ fact: "fact" }] }] }); }}>＋ Ситуація</button>}</div>{(document.situations || []).map((item: Row, situationIndex: number) => <div className="qualification-rule" key={item.id}><b>{item.id}</b>{(item.all || []).map((condition: Row, conditionIndex: number) => <QualificationConditionEditor condition={condition} objects={document.objects} immutable={immutable} change={(next: Row) => patchSituation(situationIndex, { all: item.all.map((candidate: Row, index: number) => index === conditionIndex ? next : candidate) })} remove={() => patchSituation(situationIndex, { all: item.all.filter((_: Row, index: number) => index !== conditionIndex) })} key={conditionIndex} />)}{!immutable && <button onClick={() => { const object = document.objects[0]; patchSituation(situationIndex, { all: [...(item.all || []), object ? { object: object.id, state: object.initial_state } : { fact: "fact" }] }); }}>＋ Умова</button>}</div>)}</section>
      <section><div className="qualification-section-heading"><h4>BPMN 2.0 · процеси та дії</h4></div>{(document.processes || []).map((process: Row, processIndex: number) => <details className="qualification-edit-card" open key={process.id}>
        <summary><span><b>{process.name}</b><small>{process.steps?.length || 0} кроків</small></span></summary>
        <div className="form-stack"><label>Назва процесу<input readOnly={immutable} value={process.name || ""} onChange={(event) => patchProcess(processIndex, { name: event.target.value })} /></label>{(process.steps || []).map((step: Row, stepIndex: number) => {
          const target = document.objects.find((object: Row) => object.id === step.target), capabilities = Object.keys(target?.capabilities || {});
          const patchStep = (patch: Row) => setDocument((current) => current ? updateQualificationStep(current, processIndex, stepIndex, patch) : current);
          return <div className="qualification-step-editor" key={step.id}><small>{step.id} · {step.branch || "main"}</small><input aria-label={`Назва кроку ${step.id}`} readOnly={immutable} value={step.name || ""} onChange={(event) => patchStep({ name: event.target.value })} /><label>Після<input readOnly={immutable} value={(step.after || []).join(", ")} onChange={(event) => patchStep({ after: event.target.value.split(",").map((value) => value.trim()).filter(Boolean) })} /></label><label>Об’єкт<select disabled={immutable} value={step.target || ""} onChange={(event) => patchStep({ target: event.target.value || null, capability: null })}><option value="">Без дії над об’єктом</option>{document.objects.map((object: Row) => <option value={object.id} key={object.id}>{object.name}</option>)}</select></label>{step.target && <label>Дія<select disabled={immutable} value={step.capability || ""} onChange={(event) => patchStep({ capability: event.target.value || null })}><option value="">Оберіть дію…</option>{capabilities.map((capability) => <option value={capability} key={capability}>{capability}</option>)}</select></label>}{!immutable && <button className="danger-action" onClick={() => patchProcess(processIndex, { steps: process.steps.filter((_: Row, index: number) => index !== stepIndex) })}>Видалити крок</button>}</div>;
        })}{!immutable && <button onClick={() => { const name = window.prompt("Назва нового кроку процесу"); if (!name) return; const id = uniqueDefinitionId(name, process.steps); patchProcess(processIndex, { steps: [...process.steps, { id, name, after: process.steps.length ? [process.steps.at(-1).id] : [], target: null, capability: null, branch: "main", required: true }] }); }}>＋ Додати крок</button>}</div>
      </details>)}</section>
      <section><h4>Першоджерела</h4>{(document.sources || []).map((source: Row) => <a href={source.url} target="_blank" rel="noreferrer" key={source.id}>{source.scope}</a>)}</section>
      {report?.checksum && <small className="qualification-checksum">Snapshot checksum: {report.checksum}</small>}
    </>}
  </div>;
}

function uniqueDefinitionId(name: string, rows: Row[]): string {
  const base = name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9а-яіїєґ]+/giu, "_").replace(/^_+|_+$/g, "") || "item",
    ids = new Set(rows.map((item) => item.id));
  let candidate = base, suffix = 2;
  while (ids.has(candidate)) candidate = `${base}_${suffix++}`;
  return candidate;
}

function QualificationConditionEditor({ condition, objects, immutable, change, remove }: any) {
  const type = condition.object ? "object" : condition.fact ? "fact" : "metric",
    object = objects.find((item: Row) => item.id === condition.object) || objects[0],
    switchType = (next: string) => {
      if (next === "object") change({ object: objects[0]?.id || "", state: objects[0]?.initial_state || "" });
      else if (next === "fact") change({ fact: "fact" });
      else change({ metric: "metric", at_least: 0 });
    };
  return <div className="qualification-condition-editor">
    <select aria-label="Тип умови" disabled={immutable} value={type} onChange={(event) => switchType(event.target.value)}><option value="object">Стан об’єкта</option><option value="fact">Факт</option><option value="metric">Показник</option></select>
    {type === "object" && <><select aria-label="Об’єкт умови" disabled={immutable} value={condition.object || ""} onChange={(event) => { const next = objects.find((item: Row) => item.id === event.target.value); change({ object: event.target.value, state: next?.initial_state || next?.states?.[0] || "" }); }}>{objects.map((item: Row) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><select aria-label="Стан умови" disabled={immutable} value={condition.state || ""} onChange={(event) => change({ object: object?.id || condition.object, state: event.target.value })}>{(object?.states || []).map((state: string) => <option value={state} key={state}>{state}</option>)}</select></>}
    {type === "fact" && <input aria-label="Назва факту" readOnly={immutable} value={condition.fact || ""} onChange={(event) => change({ fact: event.target.value })} />}
    {type === "metric" && <><input aria-label="Назва показника" readOnly={immutable} value={condition.metric || ""} onChange={(event) => change({ ...condition, metric: event.target.value })} /><select aria-label="Порівняння показника" disabled={immutable} value={condition.equals !== undefined ? "equals" : "at_least"} onChange={(event) => change(event.target.value === "equals" ? { metric: condition.metric, equals: Number(condition.at_least ?? 0) } : { metric: condition.metric, at_least: Number(condition.equals ?? 0) })}><option value="equals">дорівнює</option><option value="at_least">не менше</option></select><input aria-label="Значення показника" readOnly={immutable} type="number" value={condition.equals ?? condition.at_least ?? 0} onChange={(event) => change(condition.equals !== undefined ? { metric: condition.metric, equals: Number(event.target.value) } : { metric: condition.metric, at_least: Number(event.target.value) })} /></>}
    {!immutable && <button aria-label="Видалити умову" onClick={remove}>×</button>}
  </div>;
}

function updateQualificationStep(document: Row, processIndex: number, stepIndex: number, patch: Row): Row {
  const processes = structuredClone(document.processes || []), process = processes[processIndex], previous = process.steps[stepIndex], next = { ...previous, ...patch };
  process.steps[stepIndex] = next;
  let action: Row | null = null;
  if (next.target && next.capability) {
    const object = (document.objects || []).find((item: Row) => item.id === next.target), transition = object?.capabilities?.[next.capability]?.[0];
    if (transition?.to) action = { target: next.target, capability: next.capability, postcondition: { state: transition.to } };
  }
  const timeline = (document.timeline || []).map((item: Row) => item.process?.id === process.id && item.process?.step === next.id ? { ...item, action } : item);
  return { ...document, processes, timeline };
}

function QualificationMap({ document }: { document: Row }) {
  const objects = document.objects || [], steps = (document.processes || []).flatMap((process: Row) => process.steps.map((step: Row) => ({ ...step, processName: process.name }))), situations = document.situations || [],
    rowHeight = 58, height = Math.max(objects.length, steps.length, situations.length, 4) * rowHeight + 30,
    objectY = Object.fromEntries(objects.map((item: Row, index: number) => [item.id, 20 + index * rowHeight])),
    stepY = Object.fromEntries(steps.map((item: Row, index: number) => [item.id, 20 + index * rowHeight]));
  return <div className="qualification-map-scroll"><svg className="qualification-map" viewBox={`0 0 760 ${height}`} role="img" aria-label="Зв’язки об’єктів, процесів і ситуацій">
    <defs><marker id="qualification-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>
    {steps.filter((step: Row) => step.target && objectY[step.target] !== undefined).map((step: Row) => <line className="qualification-map-link action" x1="190" y1={objectY[step.target] + 17} x2="280" y2={stepY[step.id] + 17} markerEnd="url(#qualification-arrow)" key={`target-${step.id}`} />)}
    {situations.flatMap((situation: Row, situationIndex: number) => (situation.all || []).filter((condition: Row) => condition.object && objectY[condition.object] !== undefined).map((condition: Row, index: number) => <line className="qualification-map-link decision" x1="190" y1={objectY[condition.object] + 17} x2="570" y2={20 + situationIndex * rowHeight + 17} markerEnd="url(#qualification-arrow)" key={`${situation.id}-${index}`} />))}
    <text className="qualification-map-title" x="10" y="12">ОБ’ЄКТИ / UML</text><text className="qualification-map-title" x="280" y="12">КРОКИ / BPMN</text><text className="qualification-map-title" x="570" y="12">СИТУАЦІЇ / DMN</text>
    {objects.map((item: Row, index: number) => <g transform={`translate(10 ${20 + index * rowHeight})`} key={item.id}><rect className="qualification-map-node object" width="180" height="40" rx="4" /><text x="9" y="16">{item.name.slice(0, 25)}</text><text className="sub" x="9" y="31">{item.initial_state}</text></g>)}
    {steps.map((item: Row, index: number) => <g transform={`translate(280 ${20 + index * rowHeight})`} key={`${item.processName}-${item.id}`}><rect className="qualification-map-node step" width="250" height="40" rx="4" /><text x="9" y="16">{item.name.slice(0, 34)}</text><text className="sub" x="9" y="31">{item.target ? `${item.target} · ${item.capability || "дію не задано"}` : item.branch || "main"}</text></g>)}
    {situations.map((item: Row, index: number) => <g transform={`translate(570 ${20 + index * rowHeight})`} key={item.id}><rect className="qualification-map-node situation" width="180" height="40" rx="18" /><text x="9" y="24">{item.id.slice(0, 25)}</text></g>)}
  </svg></div>;
}

function EventProgramForm({ program, immutable, mutate }: { program: EventProgram; immutable: boolean; mutate: any }) {
  return <div className="event-program-form form-stack">
    <label>Назва<input readOnly={immutable} defaultValue={program.name} onBlur={(event) => event.target.value !== program.name && mutate(() => api.updateEventProgram(program.id, { name: event.target.value }))} /></label>
    <label>Тип події<input readOnly value={program.event_type_id} /></label>
    <label>Опис<textarea readOnly={immutable} defaultValue={program.description || ""} onBlur={(event) => event.target.value !== program.description && mutate(() => api.updateEventProgram(program.id, { description: event.target.value }))} /></label>
    <label className="check-row"><input disabled={immutable} type="checkbox" checked={program.enabled} onChange={(event) => mutate(() => api.updateEventProgram(program.id, { enabled: event.target.checked }))} /> Використовувати програму для цього типу</label>
    {!program.enabled && <small className="field-hint">Події цього типу оброблятимуться сумісним legacy-механізмом.</small>}
    {!immutable && <button className="danger-action" onClick={() => window.confirm(`Видалити програму «${program.name}»?`) && mutate(() => api.deleteEventProgram(program.id))}>Видалити програму</button>}
  </div>;
}

function eventActionTargetName(action: EventAction, nodes: Row[], edges: Row[]): string {
  if (action.action_type === "event.emit") return action.config?.event_type || "наступна подія";
  if (!action.target_id) return "ціль не визначена";
  return (action.target_kind === "connection" ? edges : nodes).find((item) => item.id === action.target_id)?.name || edges.find((item) => item.id === action.target_id)?.connection_key || action.target_id;
}

function defaultAction(type: EventActionType, program: EventProgram, rows: Row[], edges: Row[], components: Row[], eventTypes: Row[]): Row {
  if (type === "event.emit") return { action_type: type, target_kind: null, target_id: null, config: { event_type: eventTypes.find((item) => item.id !== program.event_type_id)?.id || eventTypes[0]?.id || "", delay_seconds: 0, payload: {} } };
  if (type.startsWith("connection.")) return { action_type: type, target_kind: "connection", target_id: edges[0]?.id || null, config: {} };
  if (type === "component.patch_state") {
    const target = compatibleEventTargets({ action_type: type, config: {} }, rows, edges, components)[0], key = target && Object.keys(target.state)[0];
    return { action_type: type, target_kind: "component", target_id: target?.id || null, config: { patch: key && target ? { [key]: target.state[key] } : {} } };
  }
  const target = rows.find((item) => item.refinement_contract?.capabilities?.length) || rows[0];
  return { action_type: type, target_kind: "component", target_id: target?.id || null, config: { capability: target?.refinement_contract?.capabilities?.[0] || "", payload: {}, forward_payload: true } };
}

function EventActionCreator({ program, graphRows, edgeRows, components, eventTypes, mutate, selectAction }: any) {
  const [type, setType] = useState<EventActionType>("component.capability");
  return <div className="event-action-create"><select aria-label="Тип нової дії" value={type} onChange={(event) => setType(event.target.value as EventActionType)}>{CREATABLE_EVENT_ACTION_TYPES.map((id) => <option value={id} key={id}>{EVENT_ACTION_LABELS[id]}</option>)}</select><button onClick={() => mutate(async () => {
    const sequence = Math.max(0, ...program.actions.map((item: EventAction) => item.sequence)) + 10;
    const created = await api.createEventAction(program.id, { ...defaultAction(type, program, graphRows, edgeRows, components, eventTypes), sequence, x: (program.layout?.trigger?.x || 40) + 240 + program.actions.length * 190, y: (program.layout?.trigger?.y || 60) + 30 });
    selectAction(created.id);
  })}>＋ Додати дію</button></div>;
}

function EventActionForm({ action, program, graphRows, edgeRows, components, eventTypes, immutable, mutate, selectAction }: any) {
  const actions = orderedActions(program), index = actions.findIndex((item) => item.id === action.id),
    targets = compatibleEventTargets(action, graphRows, edgeRows, components),
    selectedTarget = targets.find((item) => item.id === action.target_id) || eventTargetsForCurrent(action, graphRows, edgeRows, components).find((item) => item.id === action.target_id),
    typeSchema = eventTypes.find((item: Row) => item.id === program.event_type_id)?.payload_schema || {};
  const changeType = (type: EventActionType) => mutate(() => api.updateEventAction(action.id, defaultAction(type, program, graphRows, edgeRows, components, eventTypes)));
  const actionTypes = action.action_type === "component.patch_state"
    ? ["component.patch_state" as EventActionType, ...CREATABLE_EVENT_ACTION_TYPES]
    : CREATABLE_EVENT_ACTION_TYPES;
  return <div className="event-action-form form-stack">
    <PaneTitle small={`ДІЯ ${action.sequence}`} title={EVENT_ACTION_LABELS[action.action_type as EventActionType]} />
    <label>Тип дії<select disabled={immutable} value={action.action_type} onChange={(event) => changeType(event.target.value as EventActionType)}>{actionTypes.map((id) => <option value={id} key={id}>{EVENT_ACTION_LABELS[id]}</option>)}</select></label>
    {action.action_type === "component.patch_state" && <p className="field-hint">Це застаріла дія. Вона доступна лише для міграції: оберіть capability або видаліть її.</p>}
    {!immutable && <div className="sequence-actions"><button disabled={index <= 0} onClick={() => mutate(() => api.updateEventAction(action.id, { sequence: actions[index - 1].sequence }))}>↑ Раніше</button><button disabled={index < 0 || index >= actions.length - 1} onClick={() => mutate(() => api.updateEventAction(action.id, { sequence: actions[index + 1].sequence }))}>↓ Пізніше</button></div>}
    {actionTargetKind(action.action_type) && <label>Об’єкт<select disabled={immutable || action.action_type === "component.patch_state"} value={action.target_id || ""} onChange={(event) => {
      const target = targets.find((item) => item.id === event.target.value), body: Row = { target_kind: actionTargetKind(action.action_type), target_id: event.target.value || null };
      if (action.action_type === "component.patch_state" && target) {
        const key = Object.keys(target.state)[0];
        body.config = { patch: key ? { [key]: target.state[key] } : {} };
      }
      mutate(() => api.updateEventAction(action.id, body));
    }}>{!targets.length && <option value="">Немає сумісних об’єктів</option>}{targets.map((target) => <option value={target.id} key={target.id}>{target.name}</option>)}</select></label>}
    {action.action_type === "component.capability" && <>
      <label>Capability<select disabled={immutable} value={action.config?.capability || ""} onChange={(event) => mutate(() => api.updateEventAction(action.id, { config: { ...action.config, capability: event.target.value } }))}><option value="">Оберіть…</option>{[...new Set(selectedTarget?.capabilities || eventTargetsForCurrent(action, graphRows, edgeRows, components).flatMap((target) => target.capabilities))].map((capability) => <option value={capability} key={capability}>{capability}</option>)}</select></label>
      <label className="check-row"><input disabled={immutable} type="checkbox" checked={action.config?.forward_payload !== false} onChange={(event) => mutate(() => api.updateEventAction(action.id, { config: { ...action.config, forward_payload: event.target.checked } }))} /> Передавати payload тригера</label>
      <PayloadFields schema={typeSchema} value={action.config?.payload || {}} disabled={immutable} change={(payload) => mutate(() => api.updateEventAction(action.id, { config: { ...action.config, payload } }))} label="Фіксовані параметри" />
    </>}
    {action.action_type === "component.patch_state" && <StatePatchFields state={selectedTarget?.state || {}} patch={action.config?.patch || {}} disabled change={(patch) => mutate(() => api.updateEventAction(action.id, { config: { patch } }))} />}
    {action.action_type === "event.emit" && <>
      <label>Наступна подія<select disabled={immutable} value={action.config?.event_type || ""} onChange={(event) => mutate(() => api.updateEventAction(action.id, { config: { ...action.config, event_type: event.target.value, payload: payloadDefaults(eventTypes.find((item: Row) => item.id === event.target.value)?.payload_schema) } }))}>{eventTypes.map((type: Row) => <option value={type.id} key={type.id}>{type.name}</option>)}</select></label>
      <label>Затримка, секунд<input disabled={immutable} type="number" min="0" defaultValue={action.config?.delay_seconds || 0} onBlur={(event) => mutate(() => api.updateEventAction(action.id, { config: { ...action.config, delay_seconds: Number(event.target.value) } }))} /></label>
      <PayloadFields schema={eventTypes.find((item: Row) => item.id === action.config?.event_type)?.payload_schema || {}} value={action.config?.payload || {}} disabled={immutable} change={(payload) => mutate(() => api.updateEventAction(action.id, { config: { ...action.config, payload } }))} label="Payload наступної події" />
    </>}
    {!immutable && <button className="danger-action" onClick={() => window.confirm("Видалити цю дію?") && mutate(async () => { await api.deleteEventAction(action.id); selectAction(null); })}>Видалити дію</button>}
  </div>;
}

function eventTargetsForCurrent(action: EventAction, rows: Row[], edges: Row[], components: Row[]) {
  return compatibleEventTargets({ ...action, config: action.action_type === "component.capability" ? { ...action.config, capability: "" } : action.config }, rows, edges, components);
}

function StatePatchFields({ state, patch, disabled, change }: { state: Row; patch: Row; disabled: boolean; change: (value: Row) => void }) {
  const cast = (raw: string | boolean, sample: any) => typeof sample === "boolean" ? Boolean(raw) : typeof sample === "number" ? Number(raw) : String(raw);
  return <fieldset className="typed-fields"><legend>Поля стану</legend>{Object.entries(state).map(([key, sample]) => {
    const active = Object.hasOwn(patch, key);
    return <div className="state-patch-field" key={key}><label className="check-row"><input disabled={disabled} type="checkbox" checked={active} onChange={(event) => { const next = { ...patch }; if (event.target.checked) next[key] = sample; else delete next[key]; change(next); }} /> {key}</label>{active && (typeof sample === "boolean" ? <select disabled={disabled} value={String(patch[key])} onChange={(event) => change({ ...patch, [key]: event.target.value === "true" })}><option value="true">true</option><option value="false">false</option></select> : <input disabled={disabled} type={typeof sample === "number" ? "number" : "text"} defaultValue={String(patch[key] ?? "")} onBlur={(event) => change({ ...patch, [key]: cast(event.target.value, sample) })} />)}</div>})}{!Object.keys(state).length && <p>Компонент не декларує полів початкового стану.</p>}</fieldset>;
}

function PayloadFields({ schema, value, disabled, change, label = "Payload" }: { schema: Row; value: Row; disabled: boolean; change: (value: Row) => void; label?: string }) {
  const properties = schema?.properties || {};
  return <fieldset className="typed-fields"><legend>{label}</legend>{Object.entries(properties).map(([key, property]: [string, any]) => <label key={key}>{property.title || key}{schema?.required?.includes(key) ? " *" : ""}{property.type === "boolean" ? <input disabled={disabled} type="checkbox" checked={Boolean(value[key])} onChange={(event) => change({ ...value, [key]: event.target.checked })} /> : <input disabled={disabled} required={schema?.required?.includes(key)} type={["number", "integer"].includes(property.type) ? "number" : "text"} min={property.minimum} max={property.maximum} step={property.type === "integer" ? 1 : "any"} defaultValue={String(value[key] ?? property.default ?? "")} onBlur={(event) => change({ ...value, [key]: coerceSchemaValue(event.target.value, property) })} />}</label>)}{!Object.keys(properties).length && <p>Тип події не має параметрів.</p>}</fieldset>;
}

function EventCalendar({ programs, eventTypes, timeline, timezone, scenario, immutable, mutate, reload }: any) {
  const enabledPrograms = programs.filter((item: EventProgram) => item.enabled),
    [programId, setProgramId] = useState(enabledPrograms[0]?.id || "");
  useEffect(() => { if (!enabledPrograms.some((item: EventProgram) => item.id === programId)) setProgramId(enabledPrograms[0]?.id || ""); }, [enabledPrograms, programId]);
  const add = () => {
    const program = enabledPrograms.find((item: EventProgram) => item.id === programId);
    if (!program) return;
    const type = eventTypes.find((item: Row) => item.id === program.event_type_id);
    mutate(async () => { await api.createScenarioEvent(scenario, { event_type: program.event_type_id, offset_seconds: 0, priority: 100, phase: "scenario", title: program.name, payload: payloadDefaults(type?.payload_schema), evidence_kind: "assumed" }); await reload(); }, false);
  };
  return <div className="event-editor-pane event-calendar"><PaneTitle small="СЦЕНАРІЙ" title="Календар подій" />
    {!immutable && <div className="calendar-create"><select value={programId} onChange={(event) => setProgramId(event.target.value)}><option value="">Оберіть активну програму…</option>{enabledPrograms.map((item: EventProgram) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><button disabled={!programId} onClick={add}>＋ Запланувати</button></div>}
    {!enabledPrograms.length && <p className="field-hint">Спочатку увімкніть програму події, яку потрібно запланувати.</p>}
    {(timeline?.events || []).map((item: Row) => <ScenarioEventForm key={item.id} item={item} timelineStart={timeline?.start} timezone={timezone} programs={programs} eventTypes={eventTypes} immutable={immutable} mutate={mutate} reload={reload} />)}
    {!timeline?.events?.length && <p>У сценарії ще немає запланованих подій.</p>}
  </div>;
}

function ScenarioEventForm({ item, timelineStart, timezone, programs, eventTypes, immutable, mutate, reload }: any) {
  const program = programs.find((candidate: EventProgram) => candidate.event_type_id === item.event_type), type = eventTypes.find((candidate: Row) => candidate.id === item.event_type),
    selectablePrograms = programs.filter((candidate: EventProgram) => candidate.enabled || candidate.id === program?.id),
    absolute = formatZonedDateTimeLocal(new Date(new Date(timelineStart).getTime() + Number(item.offset_seconds || 0) * 1000), timezone),
    patch = (body: Row) => mutate(async () => { await api.updateScenarioEvent(item.id, body); await reload(); }, false);
  return <details className="scenario-event-card"><summary><span><b>{item.title || program?.name || item.event_type}</b><small>T+{item.offset_seconds} с · {item.phase}</small></span><em>{item.evidence_kind}</em></summary><div className="form-stack">
    <label>Програма<select disabled={immutable} value={program?.id || ""} onChange={(event) => { const next = selectablePrograms.find((candidate: EventProgram) => candidate.id === event.target.value), nextType = eventTypes.find((candidate: Row) => candidate.id === next?.event_type_id); if (next) patch({ event_type: next.event_type_id, title: next.name, payload: payloadDefaults(nextType?.payload_schema), target: null }); }}>{!program && <option value="">Legacy: {item.event_type}</option>}{selectablePrograms.map((candidate: EventProgram) => <option value={candidate.id} key={candidate.id}>{candidate.name}{!candidate.enabled ? " (вимкнена)" : ""}</option>)}</select></label>
    <label>Дата й час ({timezone})<input disabled={immutable} type="datetime-local" value={absolute} onChange={(event) => { const instant = parseZonedDateTimeLocal(event.target.value, timezone); if (instant) patch({ offset_seconds: Math.max(0, Math.round((instant.getTime() - new Date(timelineStart).getTime()) / 1000)) }); }} /></label>
    <label>Зміщення, секунд<input key={`offset:${item.offset_seconds}`} disabled={immutable} type="number" min="0" step="1" defaultValue={item.offset_seconds} onBlur={(event) => patch({ offset_seconds: Math.max(0, Math.round(Number(event.target.value) || 0)) })} /></label>
    <label>Назва<input readOnly={immutable} defaultValue={item.title || ""} onBlur={(event) => event.target.value !== item.title && patch({ title: event.target.value })} /></label>
    <label>Пріоритет<input disabled={immutable} type="number" defaultValue={item.priority} onBlur={(event) => patch({ priority: Number(event.target.value) })} /></label>
    <label>Фаза<input readOnly={immutable} defaultValue={item.phase || "scenario"} onBlur={(event) => event.target.value !== item.phase && patch({ phase: event.target.value })} /></label>
    <label>Доказовість<select disabled={immutable} value={item.evidence_kind || "assumed"} onChange={(event) => patch({ evidence_kind: event.target.value })}><option value="assumed">assumed</option><option value="derived">derived</option><option value="reported">reported</option></select></label>
    <label>Джерело<input readOnly={immutable} type="url" defaultValue={item.source_url || ""} placeholder="https://…" onBlur={(event) => event.target.value !== (item.source_url || "") && patch({ source_url: event.target.value || null })} /></label>
    <label>Примітка до джерела<textarea readOnly={immutable} defaultValue={item.source_note || ""} onBlur={(event) => event.target.value !== (item.source_note || "") && patch({ source_note: event.target.value })} /></label>
    <PayloadFields schema={type?.payload_schema || {}} value={item.payload || {}} disabled={immutable} change={(payload) => patch({ payload })} />
    {!immutable && <button className="danger-action" onClick={() => window.confirm("Видалити заплановану подію?") && mutate(async () => { await api.deleteScenarioEvent(item.id); await reload(); }, false)}>Видалити з календаря</button>}
  </div></details>;
}

function Palette({
  mode,
  w,
  disabled,
}: {
  mode: string;
  w: Workspace;
  disabled: boolean;
}) {
  const blocks = [
    "Start",
    "Event Trigger",
    "Timer",
    "Condition",
    "Python Action",
    "Wait",
    "Resource Operation",
    "Component Command",
    "Metric Check",
    "Manual Command",
    "Parallel Split",
    "Join",
    "Success",
    "Failure",
  ];
  return (
    <aside className="palette">
      <PaneTitle
        small={mode === "model" ? "БАЗА ЕЛЕМЕНТІВ" : "БЛОКИ ПРОЦЕСУ"}
        title={mode === "model" ? "Компоненти" : "Workflow"}
      />
      <input placeholder="Пошук…" />
      <div className="palette-list">
        {(mode === "model"
          ? w.components
          : blocks.map((x) => ({
              id: x.toLowerCase().replaceAll(" ", "_"),
              name: x,
              category: x.toLowerCase().replaceAll(" ", "_"),
            }))
        ).map((c: any) => (
          <button
            disabled={disabled}
            draggable={!disabled}
            onDragStart={(e) => e.dataTransfer.setData(mode, c.id)}
            key={c.id}
          >
            <i style={{ background: palette[c.category] || "#64d8cb" }} />
            <span>
              <b>{c.name}</b>
              <small>
                {mode === "model"
                  ? `${c.level} · ${c.category}`
                  : "scriptable block"}
              </small>
            </span>
            <em>⠿</em>
          </button>
        ))}
      </div>
      <p>
        {disabled
          ? "Published snapshot доступний для запуску й діагностики."
          : "Перетягніть блок на полотно та з’єднайте переходи."}
      </p>
    </aside>
  );
}

function RightPanel({
  run,
  timeline,
  selected,
  selectedEdge,
  component,
  w,
  viewVersion,
  scenario,
  workflow,
  sideTab,
  setSideTab,
  detailTab,
  setDetailTab,
  immutable,
  test,
  setTest,
  mutate,
  mode,
}: any) {
  const tabs = [
      "element",
      "events",
      "process",
      "repair",
      "resources",
      "control",
      "diagnostics",
    ],
    labels: any = {
      element: "Елемент",
      events: "Події",
      process: "Опис процесу",
      repair: "Ремонт",
      resources: "Ресурси",
      control: "Керування",
      diagnostics: "Діагностика",
    };
  return (
    <aside className="inspector">
      <div className="side-tabs">
        {tabs.map((x) => (
          <button
            className={sideTab === x ? "active" : ""}
            onClick={() => setSideTab(x)}
            key={x}
          >
            {labels[x]}
          </button>
        ))}
      </div>
      {sideTab === "element" && (
        <>
          <PaneTitle
            small="ІНСПЕКТОР"
            title={selected?.name || selectedEdge?.connection_key || "Оберіть вузол або зв’язок"}
          />
          {selectedEdge && mode === "process" && <div className="workflow-element-form"><small>SEQUENCE FLOW</small><b>{selectedEdge.condition_code || "True"}</b>{!immutable && <button className="danger-action" onClick={() => mutate(() => api.deleteWorkflowTransition(selectedEdge.id))}>Видалити перехід</button>}</div>}
          {selectedEdge && mode !== "process" && <ConnectionInspector edge={selectedEdge} immutable={immutable} mutate={mutate} w={w} version={viewVersion} />}
          {selected && (
            <>
              <div className="tabs">
                {["parameters", "ports", "monitoring", "state", "python", "test"].map((x) => (
                  <button
                    className={detailTab === x ? "active" : ""}
                    onClick={() => setDetailTab(x)}
                    key={x}
                  >
                    {x}
                  </button>
                ))}
              </div>
              {detailTab === "parameters" && (
                selected.component_type_id ? <ParameterForm readOnly={immutable} schema={component?.parameters_schema || {}} value={selected.parameters || {}} save={(parameters) => mutate(() => api.updateNode(selected.id, { parameters }))} /> : <WorkflowElementForm node={selected} readOnly={immutable} save={(value) => mutate(() => api.updateWorkflowNode(selected.id, value))} />
              )}{" "}
              {detailTab === "ports" && <PortInspector component={component} overrides={selected.port_overrides} />}{" "}
              {detailTab === "monitoring" && <MonitorPicker w={w} version={viewVersion} target={selected} immutable={immutable} mutate={mutate} />}{" "}
              {detailTab === "state" && (
                <JsonEditor
                  readOnly={immutable}
                  label="Початковий стан"
                  value={selected.initial_state || {}}
                  save={(v: any) =>
                    mutate(() =>
                      api.updateNode(selected.id, { initial_state: v }),
                    )
                  }
                />
              )}{" "}
              {detailTab === "python" && (
                <CodeEditor
                  readOnly={immutable}
                  value={selected.python_code || component?.python_code || ""}
                  save={(v: string) =>
                    mutate(() =>
                      selected.component_type_id
                        ? api.updateNode(selected.id, { python_code: v })
                        : api.updateWorkflowNode(selected.id, {
                            python_code: v,
                          }),
                    )
                  }
                />
              )}{" "}
              {detailTab === "test" && (
                <div className="test">
                  <button
                    onClick={async () =>
                      component &&
                      setTest(
                        await api.testComponent(component.id, {
                          python_code: selected.python_code,
                          parameters: selected.parameters,
                          state: selected.initial_state,
                          action: "tick",
                          dt: component.tick_seconds,
                        }),
                      )
                    }
                  >
                    ▶ Один крок
                  </button>
                  {test && <pre>{JSON.stringify(test, null, 2)}</pre>}
                </div>
              )}
              {!immutable && selected.component_type_id && <button className="danger-action" onClick={() => mutate(() => api.deleteNode(selected.id))}>Видалити елемент</button>}
              {!immutable && mode === "process" && !selected.component_type_id && selected.block_type !== "start" && <button className="danger-action workflow-delete" onClick={() => mutate(() => api.deleteWorkflowNode(selected.id))}>Видалити BPMN-елемент</button>}
            </>
          )}
        </>
      )}
      {sideTab === "events" && (
        <><EventComposer w={w} scenario={scenario} run={run} target={selected || selectedEdge} targetKind={selectedEdge ? "connection" : "component"} mutate={mutate} /><CepBuilder w={w} scenario={scenario} mutate={mutate} /><EventList events={run?.events || []} timeline={timeline} /></>
      )}{" "}
      {sideTab === "process" && <ProcessState workflow={workflow} run={run} immutable={immutable} mutate={mutate} />}{" "}
      {sideTab === "repair" && <WorkOrders rows={run?.work_orders || []} />}{" "}
      {sideTab === "resources" && (
        <Resources
          state={run?.state?.resources}
          transactions={run?.resource_transactions}
        />
      )}{" "}
      {sideTab === "control" && <Commands workflow={workflow} run={run} />}{" "}
      {sideTab === "diagnostics" && (
        <pre>
          {JSON.stringify(
            {
              run: run?.status,
              summary: run?.summary,
              selected: selected && run?.state?.nodes?.[selected.id],
            },
            null,
            2,
          )}
        </pre>
      )}
    </aside>
  );
}

function PortInspector({ component, overrides }: { component: Row; overrides?: Row }) {
  const ports: PortContract[] = component?.ports || [];
  return <div className="contract-list">
    <small>КОНТРАКТ ПОРТІВ · v{component?.version || 1}</small>
    {ports.map((port) => <div className={`contract-port domain-${port.domain.replaceAll(".", "-")}`} key={port.key}>
      <i>{port.direction === "input" ? "→" : port.direction === "output" ? "←" : "↔"}</i>
      <span><b>{overrides?.[port.key]?.name || port.name}</b><small>{port.domain} · {port.quantity}{port.unit ? ` · ${port.unit}` : ""}</small></span>
      {port.required && <em>обов’язковий</em>}
    </div>)}
  </div>;
}

function ConnectionInspector({ edge, immutable, mutate, w, version }: any) {
  return <div className="connection-inspector">
    <div className="connection-route"><b>{edge.source_port}</b><i>→</i><b>{edge.target_port}</b></div>
    <dl><dt>Середовище</dt><dd>{edge.domain || edge.flow_type}</dd><dt>Стан</dt><dd>{edge.enabled === false ? "вимкнено" : "активний"}</dd></dl>
    <JsonEditor readOnly={immutable} label="Властивості зв’язку" value={edge.parameters || {}} save={(parameters) => mutate(() => api.updateEdge(edge.id, { parameters }))} />
    <MonitorPicker w={w} version={version} target={edge} immutable={immutable} mutate={mutate} />
    {!immutable && <div className="connection-actions"><button onClick={() => mutate(() => api.updateEdge(edge.id, { enabled: edge.enabled === false }))}>{edge.enabled === false ? "Увімкнути" : "Вимкнути"}</button><button className="danger-action" onClick={() => mutate(() => api.deleteEdge(edge.id))}>Видалити зв’язок</button></div>}
  </div>;
}

function MonitorPicker({ w, version, target, immutable, mutate }: { w: Workspace; version: Row; target: Row; immutable: boolean; mutate: any }) {
  const available = (w.observables || []).filter((item) => item.target_id === target.id),
    [selected, setSelected] = useState("");
  useEffect(() => setSelected(available[0]?.address || ""), [target.id]);
  const add = () => {
    const observable = available.find((item) => item.address === selected);
    if (!observable) return;
    const current = (w.monitors || []).find((item) => item.model_version_id === version.id);
    const series = { address: observable.address, name: observable.name, unit: observable.unit, style: "line", color: seriesColors[(current?.panels?.[0]?.series?.length || 0) % seriesColors.length] };
    if (!current) return mutate(() => api.createMonitor(version.id, { name: "Монітор моделі", panels: [{ id: "main", name: "Основні показники", type: "chart", series: [series] }] }));
    const panels = current.panels?.length ? current.panels.map((panel: Row, index: number) => index ? panel : { ...panel, series: [...(panel.series || []).filter((item: Row) => item.address !== series.address), series] }) : [{ id: "main", name: "Основні показники", type: "chart", series: [series] }];
    return mutate(() => api.updateMonitor(current.id, { panels }));
  };
  return <div className="monitor-picker">
    <small>ДОСТУПНІ ПОКАЗНИКИ · визначені моделлю</small>
    {!available.length ? <p>Цей елемент поки не публікує показників.</p> : <>
      <select value={selected} onChange={(event) => setSelected(event.target.value)}>{available.map((item) => <option value={item.address} key={item.address}>{item.name}{item.unit ? ` · ${item.unit}` : ""}</option>)}</select>
      <button disabled={immutable} onClick={add}>＋ Додати до моніторингу</button>
      {immutable && <p>Створіть draft-копію моделі, щоб змінити шаблон моніторингу.</p>}
    </>}
  </div>;
}

function EventComposer({ w, scenario, run, target, targetKind, mutate }: any) {
  const definitions = (w.event_types || []).filter((item: Row) => !target || !item.target_kinds?.length || item.target_kinds.includes(targetKind)),
    [eventType, setEventType] = useState("");
  useEffect(() => setEventType(definitions[0]?.id || ""), [target?.id, targetKind, w.event_types?.length]);
  if (!target) return <div className="event-composer"><small>НОВА ПОДІЯ</small><p>Оберіть елемент або зв’язок на схемі, щоб створити подію з правильною ціллю.</p></div>;
  const submit = () => {
    if (!eventType) return;
    const body = { event_type: eventType, target: { kind: targetKind, id: target.id, key: target.instance_key || target.connection_key || target.id }, payload: {}, title: definitions.find((item: Row) => item.id === eventType)?.name || eventType };
    return mutate(() => run?.id ? api.inject(run.id, body) : api.createScenarioEvent(scenario, { ...body, offset_seconds: 0 }));
  };
  return <div className="event-composer"><small>НОВА ТИПІЗОВАНА ПОДІЯ</small><b>{target.name || target.connection_key}</b><select value={eventType} onChange={(event) => setEventType(event.target.value)}>{definitions.map((item: Row) => <option value={item.id} key={item.id}>{item.name}</option>)}</select><button disabled={!eventType} onClick={submit}>{run?.id ? "Додати до run" : "Додати до сценарію"}</button></div>;
}

function CepBuilder({ w, scenario, mutate }: any) {
  const numeric = (w.observables || []).filter((item: Row) => item.value_type === "number"),
    [operator, setOperator] = useState("compare"), [observable, setObservable] = useState(numeric[0]?.address || "system:frequency_hz"),
    [comparison, setComparison] = useState("lt"), [value, setValue] = useState(49.5), [windowSeconds, setWindowSeconds] = useState(30),
    [eventTypes, setEventTypes] = useState("line_fault, trip"), [emit, setEmit] = useState((w.event_types || [])[0]?.id || "alert");
  const save = () => {
    const metric = { operator: "compare", observable, comparison, value, hysteresis: 0.02 }, event = { operator: "event", event_type: eventTypes.split(",")[0].trim(), window_seconds: windowSeconds };
    let condition: Row = metric;
    if (["average", "min", "max", "trend"].includes(operator)) condition = { operator, observable, comparison, value, window_seconds: windowSeconds };
    else if (operator === "duration") condition = { operator, seconds: windowSeconds, condition: metric };
    else if (operator === "absence") condition = { operator, window_seconds: windowSeconds, condition: event };
    else if (operator === "sequence") condition = { operator, event_types: eventTypes.split(",").map((item) => item.trim()).filter(Boolean), window_seconds: windowSeconds };
    else if (operator === "count") condition = { operator, condition: event, comparison, value, window_seconds: windowSeconds };
    else if (["all", "any"].includes(operator)) condition = { operator, conditions: [metric, event] };
    else if (operator === "not") condition = { operator, condition: metric };
    return mutate(() => api.createCepRule(scenario, { name: `${operator} → ${emit}`, condition, emit: { event_type: emit, payload: {} } }));
  };
  return <details className="cep-builder"><summary>＋ Правило автоматичного виявлення (CEP)</summary><div><label>Умова<select value={operator} onChange={(event) => setOperator(event.target.value)}>{["compare", "all", "any", "not", "sequence", "count", "average", "min", "max", "duration", "absence", "trend"].map((item) => <option key={item}>{item}</option>)}</select></label>{!["sequence", "absence"].includes(operator) && <label>Показник<select value={observable} onChange={(event) => setObservable(event.target.value)}>{numeric.map((item: Row) => <option value={item.address} key={item.address}>{item.target_name} · {item.name}</option>)}</select></label>}<label>Порівняння<select value={comparison} onChange={(event) => setComparison(event.target.value)}>{["lt", "lte", "gt", "gte", "eq", "ne"].map((item) => <option key={item}>{item}</option>)}</select></label><label>Значення<input type="number" value={value} onChange={(event) => setValue(Number(event.target.value))} /></label><label>Вікно / тривалість, с<input type="number" value={windowSeconds} onChange={(event) => setWindowSeconds(Number(event.target.value))} /></label><label>Послідовність подій<input value={eventTypes} onChange={(event) => setEventTypes(event.target.value)} /></label><label>Створити подію<select value={emit} onChange={(event) => setEmit(event.target.value)}>{(w.event_types || []).map((item: Row) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label><button onClick={save}>Зберегти правило</button></div></details>;
}

function EventList({
  events,
  timeline,
}: {
  events: Row[];
  timeline: Row | null;
}) {
  const [view, setView] = useState<"programmed" | "monitor">("monitor"),
    timelineStart = timeline?.start || new Date().toISOString();
  const templates: Row[] = (timeline?.events || []).map((e: Row) => ({
    ...e,
    scheduled_at: new Date(
      new Date(timelineStart).getTime() + e.offset_seconds * 1000,
    ).toISOString(),
    status: "programmed",
    payload: {
      ...e.payload,
      _phase: e.phase,
      _title: e.title,
      _evidence: e.evidence_kind,
    },
  }));
  const programmed: Row[] = events.length ? events : templates,
    processed: Row[] = events
      .filter((e: Row) => e.status === "processed")
      .sort(
        (a: Row, b: Row) =>
          new Date(b.scheduled_at).getTime() -
          new Date(a.scheduled_at).getTime(),
      ),
    rows: Row[] = view === "monitor" ? processed : programmed;
  return (
    <div className="side-list">
      <PaneTitle
        small={view === "monitor" ? "ФАКТИЧНИЙ ЖУРНАЛ" : "КАЛЕНДАР"}
        title={
          view === "monitor"
            ? `Монітор · ${processed.length}`
            : `Запрограмовано · ${programmed.length}`
        }
      />
      <div className="event-mode">
        <button
          className={view === "programmed" ? "active" : ""}
          onClick={() => setView("programmed")}
        >
          Запрограмовані
        </button>
        <button
          className={view === "monitor" ? "active" : ""}
          onClick={() => setView("monitor")}
        >
          Монітор подій
        </button>
      </div>
      {!rows.length && (
        <p>
          {view === "monitor"
            ? "Виконані події з’являтимуться тут, найновіші — зверху."
            : "Події не запрограмовані."}
        </p>
      )}
      {rows.map((e: Row) => (
        <article key={e.id}>
          <time>{clock(e.scheduled_at)}</time>
          <b>{e.payload?._title || e.event_type}</b>
          <span>
            {e.payload?._phase || e.origin} ·{" "}
            {e.payload?._evidence || "derived"}
          </span>
          <em className={e.status}>{e.status}</em>
        </article>
      ))}
    </div>
  );
}
function ProcessState({
  workflow,
  run,
  immutable,
  mutate,
}: {
  workflow: Row | null;
  run: Row | null;
  immutable: boolean;
  mutate: (action: () => Promise<any>) => Promise<any>;
}) {
  const [description, setDescription] = useState("");
  useEffect(() => setDescription(workflow?.version?.description || ""), [workflow?.version?.id, workflow?.version?.description]);
  return (
    <div className="side-list">
      <PaneTitle
        small="WORKFLOW"
        title={workflow?.version?.name || "Не підключено"}
      />
      {workflow?.version && <div className="workflow-description-form">
        <small>ОПИС ПРОЦЕСУ · УКРАЇНСЬКОЮ</small>
        <textarea
          aria-label="Опис процесу українською мовою"
          value={description}
          readOnly={immutable}
          placeholder="Опишіть українською призначення, умову запуску, область дії та послідовність цього BPMN-процесу."
          onChange={(event) => setDescription(event.target.value)}
        />
        <span>{immutable ? "Опублікована версія · створіть чернетку для редагування" : "Цей текст належить поточній версії BPMN-схеми."}</span>
        {!immutable && <button disabled={description === (workflow.version.description || "")} onClick={() => mutate(() => api.updateWorkflow(workflow.version.id, { description }))}>Зберегти опис</button>}
      </div>}
      {workflow?.nodes?.map((n: Row) => (
        <article
          key={n.id}
          className={
            run?.workflow_instances?.[0]?.current_node_id === n.id
              ? "current"
              : ""
          }
        >
          <b>{n.name}</b>
          <span>
            {n.block_type} · {n.config?.event_type}
          </span>
        </article>
      ))}
      {run?.active_workflow_tasks?.map((task: Row) => (
        <article key={task.id} className="current">
          <small>РУЧНЕ ЗАВДАННЯ BPMN</small>
          <b>{task.node?.name}</b>
          <span>{task.node?.block_type === "user_task" ? "User Task" : "Manual Task"}</span>
          <button onClick={() => mutate(() => api.completeWorkflowTask(run.id, task.id, { completed_by: "operator" }))}>
            Позначити виконаним
          </button>
        </article>
      ))}
    </div>
  );
}
function WorkOrders({ rows }: { rows: Row[] }) {
  return (
    <div className="side-list">
      <PaneTitle small="WORK ORDERS" title={`${rows.length} заявок`} />
      {rows.map((x) => (
        <article key={x.id}>
          <b>{x.result?.logical_id || x.node_id}</b>
          <span>
            {x.status} · ремонт {x.repair_hours || 0} год
          </span>
          <em>{x.completed_at ? "закрито" : "активна"}</em>
        </article>
      ))}
    </div>
  );
}
function Resources({
  state,
  transactions,
}: {
  state: any;
  transactions: Row[];
}) {
  return (
    <div className="side-list">
      <PaneTitle small="RESOURCE LEDGER" title="Ресурси" />
      {Object.entries(state || {}).map(([id, x]: any) => (
        <article key={id}>
          <b>{id}</b>
          <span>
            доступно {x.available} · резерв {x.reserved} {x.unit}
          </span>
        </article>
      ))}
      {transactions?.map((x) => (
        <article key={x.id}>
          <time>{clock(x.model_time)}</time>
          <b>
            {x.operation} · {x.resource_id}
          </b>
          <span>
            {x.quantity} {x.unit}
          </span>
        </article>
      ))}
    </div>
  );
}
function Commands({
  workflow,
  run,
}: {
  workflow: Row | null;
  run: Row | null;
}) {
  const [message, setMessage] = useState("");
  const commands = workflow?.version?.settings?.commands || [];
  return (
    <div className="side-list">
      <PaneTitle small="SCRIPT COMMANDS" title="Керування" />
      {commands.map((c: Row) => (
        <article key={c.id}>
          <b>{c.label}</b>
          <span>{Object.keys(c.parameters || {}).join(", ")}</span>
          <button
            disabled={!run}
            onClick={async () => {
              if (!run) return;
              const result = await api.userCommand(run.id, {
                command_type: c.id,
                payload: { quantity: 2 },
              });
              setMessage(`${c.label}: ${result.status}`);
            }}
          >
            Виконати
          </button>
        </article>
      ))}
      <p>{message || "Команди оголошуються workflow-скриптом."}</p>
    </div>
  );
}

const speedPresets = [
  { value: 1, label: "1 секунда / сек" },
  { value: 60, label: "1 хвилина / сек" },
  { value: 3600, label: "1 година / сек" },
  { value: 86400, label: "1 день / сек" },
  { value: 2592000, label: "1 місяць / сек" },
  { value: 31536000, label: "1 рік / сек" },
];
function SpeedSelect({
  value,
  setValue,
  label,
}: {
  value: number;
  setValue: (n: number) => void;
  label: string;
}) {
  const preset = speedPresets.some((x) => x.value === value),
    [custom, setCustom] = useState(preset ? 30 : value / 60),
    [unit, setUnit] = useState(60);
  return (
    <label className="speed-select">
      <span>{label}</span>
      <select
        value={preset ? String(value) : "custom"}
        onChange={(e) => {
          if (e.target.value !== "custom") setValue(Number(e.target.value));
          else setValue(custom * unit);
        }}
      >
        {speedPresets.map((x) => (
          <option value={x.value} key={x.value}>
            {x.label}
          </option>
        ))}
        <option value="custom">Власна швидкість…</option>
      </select>
      {!preset && (
        <span className="custom-speed">
          <input
            type="number"
            min="0.001"
            step="any"
            value={custom}
            onChange={(e) => {
              const n = Math.max(0.001, Number(e.target.value));
              setCustom(n);
              setValue(n * unit);
            }}
          />
          <select
            value={unit}
            onChange={(e) => {
              const u = Number(e.target.value);
              setUnit(u);
              setValue(custom * u);
            }}
          >
            <option value="1">сек / сек</option>
            <option value="60">хв / сек</option>
            <option value="3600">год / сек</option>
            <option value="86400">днів / сек</option>
          </select>
        </span>
      )}
    </label>
  );
}
function Monitor({
  w,
  graphRows,
  run,
  timeline,
  scenario,
  setScenario,
  start,
  setRun,
  selectedNodeId,
  focusNode,
}: any) {
  const [idleSpeed, setIdleSpeed] = useState(3600),
    [eventSpeed, setEventSpeed] = useState(60),
    [eventDuration, setEventDuration] = useState(10),
    [eventTypes, setEventTypes] = useState<string[]>([
      "line_fault",
      "operator_error",
      "overload",
      "grid.disturbance_notified",
      "repair_completed",
      "restoration_complete",
    ]),
    [scrubMs, setScrubMs] = useState<number | null>(null),
    [timelineScale, setTimelineScale] = useState<TimelineScale>("auto"),
    [settingsOpen, setSettingsOpen] = useState(false);
  const cmd = async (action: string, body: Row = {}) =>
    run && setRun(await api.command(run.id, { action, ...body }));
  const events = run?.events?.length
    ? run.events
    : (timeline?.events || []).map((e: Row) => ({
        ...e,
        scheduled_at: new Date(
          new Date(timeline.start).getTime() + e.offset_seconds * 1000,
        ).toISOString(),
        status: "template",
        payload: {
          _phase: e.phase,
          _title: e.title,
          _evidence: e.evidence_kind,
        },
      }));
  const fullStartMs = new Date(
      timeline?.start || run?.current_time || Date.now(),
    ).getTime(),
    requestedEndMs = new Date(
      timeline?.end || run?.end_time || Date.now() + 1,
    ).getTime(),
    currentMs = new Date(
      run?.current_time || timeline?.start || Date.now(),
    ).getTime(),
    viewport = timelineViewport(fullStartMs, requestedEndMs, currentMs, events, timelineScale),
    startMs = viewport.start,
    endMs = viewport.end,
    shownMs = Math.max(startMs, Math.min(endMs, scrubMs ?? currentMs)),
    incidentMs = new Date(
      timeline?.incident || run?.incident_at || currentMs,
    ).getTime(),
    metrics = run?.summary || {},
    visibleEvents = events.filter((event: Row) => {
      const at = new Date(event.scheduled_at).getTime();
      return at >= startMs && at <= endMs;
    }),
    eventLaneCounts = visibleEvents.reduce((counts: Map<number, number>, event: Row) => {
      const at = new Date(event.scheduled_at).getTime();
      counts.set(at, (counts.get(at) || 0) + 1);
      return counts;
    }, new Map<number, number>()),
    railHeight = Math.max(38, 18 + Math.max(1, ...eventLaneCounts.values()) * 14),
    nextEvent = viewport.next === null ? null : events.find((event: Row) => Math.abs(new Date(event.scheduled_at).getTime() - viewport.next!) < 1000),
    nextInSeconds = viewport.next === null ? null : Math.max(0, Math.round((viewport.next - currentMs) / 1000));
  const definitions = w.metrics.filter((m: Row) =>
      [
        "generation_mw",
        "demand_mw",
        "unserved_mw",
        "frequency_hz",
        "voltage_pu",
        "gas_consumption_mw",
        "gas_remaining_mwh",
        "co2_tph",
      ].includes(m.id),
    ),
    flows = run?.flows || [],
    availableTypes = Array.from(
      new Set((timeline?.events || []).map((e: Row) => e.event_type)),
    ) as string[];
  const settings = {
    idle_speed: idleSpeed,
    event_speed: eventSpeed,
    event_speed_duration_seconds: eventDuration,
    event_speed_event_types: eventTypes,
  };
  const applySettings = async () => {
    if (run)
      setRun(
        await api.command(run.id, { action: "playback_settings", ...settings }),
      );
  };
  useEffect(() => {
    if (!run || scrubMs === null) return;
    const timer = window.setTimeout(async () => {
      try {
        setRun(await api.seek(run.id, { at: new Date(scrubMs).toISOString() }));
      } finally {
        setScrubMs(null);
      }
    }, 450);
    return () => window.clearTimeout(timer);
  }, [scrubMs, run?.id]);
  useEffect(() => {
    setTimelineScale("auto");
    setScrubMs(null);
  }, [scenario, run?.id]);
  return (
    <section className="console">
      <div className="transport">
        <select value={scenario} onChange={(e) => setScenario(e.target.value)}>
          {w.scenarios.filter((s: Row) => !s.configuration?.deprecated).map((s: Row) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <div className="transport-buttons">
          <button className="start" onClick={() => start(settings)}>
            ▶ Start
          </button>
          {["pause", "step", "resume", "stop"].map((x) => (
            <button disabled={!run} onClick={() => cmd(x)} key={x}>
              {x}
            </button>
          ))}
          <button
            className={settingsOpen ? "active" : ""}
            onClick={() => setSettingsOpen(!settingsOpen)}
          >
            ⚙ Швидкості
          </button>
        </div>
      </div>
      <div className="clock">
        <small>МОДЕЛЬНИЙ ЧАС · EUROPE/KYIV</small>
        <b>{clock(run?.current_time || timeline?.start)}</b>
        <span>
          {run?.status || "готовий"} · базова {run?.idle_speed || idleSpeed}× ·
          подієва {run?.event_speed || eventSpeed}×
        </span>
      </div>
      <div className="metric-grid">
        {definitions.map((m: Row) => (
          <span key={m.id}>
            <small>{m.name}</small>
            <b>
              {metrics[m.id] ?? "—"} {m.unit}
            </b>
          </span>
        ))}
      </div>
      {settingsOpen && (
        <div className="playback-settings">
          <button
            className="playback-close"
            type="button"
            aria-label="Закрити налаштування швидкостей"
            title="Закрити"
            onClick={() => setSettingsOpen(false)}
          >
            ×
          </button>
          <SpeedSelect
            label="Без подій"
            value={idleSpeed}
            setValue={setIdleSpeed}
          />
          <SpeedSelect
            label="Під час події"
            value={eventSpeed}
            setValue={setEventSpeed}
          />
          <label>
            <span>Тривалість подієвої швидкості</span>
            <input
              type="number"
              min="0"
              value={eventDuration}
              onChange={(e) => setEventDuration(Number(e.target.value))}
            />
            <small>секунд реального часу</small>
          </label>
          <div className="event-triggers">
            <span>Події-тригери</span>
            {availableTypes.map((type) => (
              <label key={type}>
                <input
                  type="checkbox"
                  checked={eventTypes.includes(type)}
                  onChange={() =>
                    setEventTypes((old) =>
                      old.includes(type)
                        ? old.filter((x) => x !== type)
                        : [...old, type],
                    )
                  }
                />
                {type}
              </label>
            ))}
          </div>
          <button onClick={applySettings} disabled={!run}>
            Застосувати до run
          </button>
        </div>
      )}
      <div className="timeline">
        <div className="timeline-head">
          <b>{clock(new Date(startMs).toISOString())}</b>
          <span>{timelineScale === "auto" ? "АВТОМАСШТАБ ДО НАСТУПНОЇ ПОДІЇ" : "ПОВНИЙ СЦЕНАРІЙ"}</span>
          <b>{clock(new Date(endMs).toISOString())}</b>
        </div>
        <div className="rail" style={{ height: `${railHeight}px` }}>
          {incidentMs >= startMs && incidentMs <= endMs && <i
            className="incident-marker"
            style={{ left: `${pct(incidentMs, startMs, endMs)}%` }}
          ><span>ІНЦИДЕНТ · {clock(timeline?.incident)}</span></i>}
          <i
            className="time-marker"
            style={{ left: `${pct(shownMs, startMs, endMs)}%` }}
          ><span>ПОТОЧНИЙ ЧАС</span></i>
          {visibleEvents.map((e: Row, i: number) => {
            const eventMs = new Date(e.scheduled_at).getTime();
            const sameTimeBefore = visibleEvents.slice(0, i).filter((candidate: Row) => new Date(candidate.scheduled_at).getTime() === eventMs).length;
            const eventPosition = pct(eventMs, startMs, endMs);
            const eventLabel = `${e.payload?._title || e.event_type}${e.payload?._source_member_alias ? ` · ${e.payload._source_member_alias} → ${e.payload._target_member_alias}` : ""}`;
            return (
            <button
              key={e.id || i}
              className={`event-dot ${eventPosition > 68 ? "label-left" : ""} ${e.payload?._phase || "scenario"} ${e.status} origin-${e.origin || "scenario"}`}
              style={{
                left: `${eventPosition}%`,
                top: `${8 + sameTimeBefore * 14}px`,
              }}
              title={`${clock(e.scheduled_at)} · ${eventLabel}`}
              aria-label={`${clock(e.scheduled_at)} · ${eventLabel}`}
              onClick={() => run && setScrubMs(eventMs)}
            ><span>{eventLabel}</span></button>
          )})}
          <input
            aria-label="Поточний модельний час"
            className="timeline-scrubber"
            type="range"
            min={startMs}
            max={endMs}
            step={60000}
            value={shownMs}
            disabled={!run}
            onChange={(e) => setScrubMs(Number(e.target.value))}
          />
        </div>
        <div className="timeline-labels">
          <span>{timelineScale === "auto" ? "автомасштаб" : "повний час"}</span>
          <strong>
            {scrubMs !== null
              ? `Перейти: ${clock(new Date(scrubMs).toISOString())}`
              : nextEvent
                ? `Далі: ${nextEvent.payload?._title || nextEvent.event_type} · через ${nextInSeconds! >= 3600 ? `${(nextInSeconds! / 3600).toFixed(1)} год` : `${Math.ceil(nextInSeconds! / 60)} хв`}`
                : `${Math.round(pct(currentMs, startMs, endMs))}%`}
          </strong>
          <span className="timeline-scale-controls"><button className={timelineScale === "auto" ? "active" : ""} onClick={() => { setTimelineScale("auto"); setScrubMs(null); }}>До наступної</button><button className={timelineScale === "full" ? "active" : ""} onClick={() => { setTimelineScale("full"); setScrubMs(null); }}>Весь сценарій</button></span>
        </div>
      </div>
      <div className="energy-monitor">
        <b>ПЕРЕХІД ЕНЕРГІЇ</b>
        <div className="energy-chain">
          <span>
            Газ <em>{metrics.gas_consumption_mw ?? 0} MWth</em>
          </span>
          <i>→</i>
          <span>
            Генерація <em>{metrics.generation_mw ?? 0} MW</em>
          </span>
          <i>→</i>
          <span>
            Споживання <em>{metrics.served_load_mw ?? 0} MW</em>
          </span>
          <i>→</i>
          <span>
            Тепло / світло{" "}
            <em>
              {metrics.heat_mw ?? 0} / {metrics.visible_radiation_mw ?? 0} MW
            </em>
          </span>
        </div>
        <div className="flow-strip">
          {flows.slice(0, 8).map((f: Row) => (
            <span key={f.id}>
              {f.flow_type} · {Number(f.value).toFixed(1)} {f.unit}
            </span>
          ))}
        </div>
      </div>
      <DiagnosticCharts
        w={w}
        nodes={graphRows}
        samples={(run?.samples || []).slice().reverse()}
        timeline={timeline}
        selectedNodeId={selectedNodeId}
        focusNode={focusNode}
      />
    </section>
  );
}

const seriesColors = ["#f0bd63", "#b7f34a", "#64d8cb", "#d899ff", "#ff8f72"];

function DiagnosticCharts({ w, nodes, samples, timeline, selectedNodeId, focusNode }: any) {
  const dashboard = w.dashboards.find((item: Row) => item.id === "energy-system-monitor"),
    rawConfigured = dashboard?.panels?.find((panel: Row) => panel.type === "diagnostic_charts")?.sets || [],
    catalog = (w.observables || []).filter((item: Row) => item.scope === "component"),
    configured = reconcileDiagnosticSets(rawConfigured, nodes, catalog),
    [sets, setSets] = useState<Row[]>(configured),
    [active, setActive] = useState(0),
    [point, setPoint] = useState(nodes[0]?.id || ""),
    [metric, setMetric] = useState(""),
    [style, setStyle] = useState("line"),
    [fromPct, setFromPct] = useState(0),
    [toPct, setToPct] = useState(100),
    [saved, setSaved] = useState("");
  useEffect(() => {
    setSets(configured);
    setActive(0);
    setFromPct(0);
    setToPct(100);
  }, [dashboard?.id, nodes?.[0]?.model_version_id]);
  const controlPoints = nodes.filter((node: Row) => catalog.some((item: Row) => item.target_id === node.id)).map((node: Row) => ({ type: node.id, node })),
    pointMetrics = catalog.filter((item: Row) => item.target_id === point && item.value_type === "number"),
    current = sets[Math.min(active, Math.max(sets.length - 1, 0))],
    fullStart = new Date(timeline?.start || samples[0]?.model_time || Date.now()).getTime(),
    fullEnd = new Date(timeline?.end || samples.at(-1)?.model_time || fullStart + 1).getTime(),
    timeMin = fullStart + (Math.max(0, fromPct) / 100) * Math.max(1, fullEnd - fullStart),
    timeMax = fullStart + (Math.min(100, toPct) / 100) * Math.max(1, fullEnd - fullStart),
    rangeSamples = samples.filter((sample: Row) => {
      const time = new Date(sample.model_time).getTime();
      return time >= timeMin && time <= timeMax;
    }),
    preceding = samples.filter((sample: Row) => new Date(sample.model_time).getTime() < timeMin).at(-1),
    visible = preceding ? [{ ...preceding, model_time: new Date(timeMin).toISOString() }, ...rangeSamples] : rangeSamples,
    incidentAt = timeline?.incident,
    metricSpec = (seriesOrId: Row | string) => {
      const series = typeof seriesOrId === "string" ? { metric: seriesOrId } : seriesOrId;
      const observable = catalog.find((item: Row) => item.address === series.address) || catalog.find((item: Row) => item.target_id === series.target_id && item.metric_key === (series.metric_key || series.metric));
      const legacy = w.metrics.find((item: Row) => item.id === (series.metric_key || series.metric));
      return observable ? { id: observable.metric_key, label: observable.name, unit: observable.unit } : legacy ? { id: legacy.id, label: legacy.name, unit: legacy.unit } : undefined;
    },
    valueAt = (sample: Row, series: Row) =>
      diagnosticSeriesValue(sample, nodes, series),
    domain = (series: Row) => {
      const values = visible.map((sample: Row) => valueAt(sample, series)).filter(Number.isFinite);
      if ((series.metric_key || series.metric) === "voltage_pu") return [0, 1.1];
      if ((series.metric_key || series.metric) === "frequency_hz") return [0, 51];
      return [Math.min(0, ...values), Math.max(1, ...values) * 1.08];
    },
    x = (sample: Row) => 48 + ((new Date(sample.model_time).getTime() - timeMin) / Math.max(1, timeMax - timeMin)) * 824,
    y = (value: number, series: Row, seriesIndex: number) =>
      diagnosticSeriesLaneY(value, domain(series) as [number, number], seriesIndex, current?.series?.length || 1),
    linePath = (series: Row, seriesIndex: number) => {
      const points = visible.map((sample: Row) => ({
        x: x(sample),
        y: y(valueAt(sample, series), series, seriesIndex),
      })).filter((point: Row) => Number.isFinite(point.y));
      if (!points.length) return "";
      const first = `M${points[0].x},${points[0].y}`;
      if (["voltage_pu", "frequency_hz"].includes(series.metric_key || series.metric)) {
        return points.slice(1).reduce(
          (path: string, point: Row) => `${path} H${point.x} V${point.y}`,
          first,
        );
      }
      return points.slice(1).reduce(
        (path: string, point: Row) => `${path} L${point.x},${point.y}`,
        first,
      );
    };
  useEffect(() => {
    const nextPoint = preferredDiagnosticPoint(nodes, catalog, selectedNodeId, point);
    if (nextPoint !== point) setPoint(nextPoint);
    if (!pointMetrics.some((item: Row) => item.address === metric)) setMetric(pointMetrics[0]?.address || "");
  }, [catalog.length, point, metric, nodes.length, selectedNodeId]);
  const updateCurrent = (next: Row) => setSets((old) => old.map((item, index) => index === active ? next : item));
  const addSeries = () => {
    if (!current) return;
    const observable = pointMetrics.find((item: Row) => item.address === metric) || pointMetrics[0];
    if (!observable) return;
    updateCurrent({
      ...current,
      series: [...current.series, { target_id: point, target_key: observable.target_key, address: observable.address, metric_key: observable.metric_key, style, color: seriesColors[current.series.length % seriesColors.length] }],
    });
  };
  const addSet = () => {
    const next = { id: `chart-${Date.now()}`, name: `Набір ${sets.length + 1}`, series: [] };
    setSets((old) => [...old, next]); setActive(sets.length);
  };
  const save = async () => {
    if (!dashboard) return;
    const panels = dashboard.panels.map((panel: Row) => panel.type === "diagnostic_charts" ? { ...panel, sets } : panel);
    await api.updateDashboard(dashboard.id, { panels });
    setSaved("Збережено"); window.setTimeout(() => setSaved(""), 1600);
  };
  const focusIncident = () => {
    if (!incidentAt) return;
    const incident = new Date(incidentAt).getTime();
    const center = ((incident - fullStart) / Math.max(1, fullEnd - fullStart)) * 100;
    const span = Math.max(.25, (12 * 3600 * 1000 / Math.max(1, fullEnd - fullStart)) * 100);
    setFromPct(Math.max(0, center - span)); setToPct(Math.min(100, center + span));
  };
  if (!current) return <div className="diagnostic-charts"><small>ГРАФІКИ · немає конфігурації</small></div>;
  return (
    <div className="diagnostic-charts">
      <div className="chart-tabs">
        {sets.map((item, index) => <button className={index === active ? "active" : ""} onClick={() => setActive(index)} key={item.id}>{item.name}</button>)}
        <button onClick={addSet} title="Новий комплект графіків">＋</button>
      </div>
      <div className="chart-config">
        <select aria-label="Точка діагностики" value={point} onChange={(e) => { const id = e.target.value; setPoint(id); setMetric(catalog.find((item: Row) => item.target_id === id && item.value_type === "number")?.address || ""); focusNode(id); }}>
          {controlPoints.map(({ type, node }: any) => <option value={type} key={type}>{node.name}</option>)}
        </select>
        <select aria-label="Параметр діагностики" value={metric} onChange={(e) => setMetric(e.target.value)}>
          {pointMetrics.map((item: Row) => <option value={item.address} key={item.address}>{item.name}{item.unit ? ` · ${item.unit}` : ""}</option>)}
        </select>
        <select aria-label="Тип графіка" value={style} onChange={(e) => setStyle(e.target.value)}><option value="line">Лінія</option><option value="bar">Стовпчики</option></select>
        <button onClick={addSeries}>＋ Ряд</button><button onClick={save}>Зберегти</button><em>{saved}</em>
      </div>
      <div className="series-legend">
        {current.series.map((series: Row, index: number) => {
          const nodeId = resolveSeriesNodeId(nodes, series);
          const node = nodes.find((item: Row) => item.id === nodeId);
          const spec = metricSpec(series);
          const stats = diagnosticSeriesStats(visible, nodes, series);
          const unit = spec?.unit ? ` ${spec.unit}` : "";
          return <span className="series-chip" key={`${series.address || series.point_component_type}-${series.metric_key || series.metric}-${index}`} style={{ "--series": series.color } as any}>
            <button className="series-summary" onClick={() => nodeId && focusNode(nodeId)} title="Показати елемент на схемі">
              <span><i />{node?.name || series.target_key || series.point_component_type} · {spec?.label || series.metric_key || series.metric}</span>
              <strong>{stats ? `${formatDiagnosticValue(stats.latest)}${unit}` : "немає даних"}</strong>
              {stats && <small>min {formatDiagnosticValue(stats.min)} · max {formatDiagnosticValue(stats.max)}{unit} · {stats.count} вим.</small>}
            </button>
            <button className="remove-series" aria-label="Прибрати ряд" title="Прибрати ряд" onClick={() => updateCurrent({ ...current, series: current.series.filter((_: Row, i: number) => i !== index) })}>×</button>
          </span>;
        })}
      </div>
      <div className="chart-canvas">
        {!visible.length ? <p>Запустіть модель, щоб накопичити історичні вимірювання.</p> : <svg viewBox="0 0 900 220" role="img" aria-label={`Діагностичний графік ${current.name}`}>
          {current.series.map((_: Row, index: number) => {
            const laneBottom = 24 + (162 / Math.max(1, current.series.length)) * (index + 1);
            return <line key={`lane-${index}`} x1="48" x2="872" y1={laneBottom} y2={laneBottom} className="grid-line" />;
          })}
          {incidentAt && new Date(incidentAt).getTime() >= timeMin && new Date(incidentAt).getTime() <= timeMax && <><line x1={x({model_time:incidentAt})} x2={x({model_time:incidentAt})} y1="24" y2="190" className="incident-line"/><text x={x({model_time:incidentAt}) + 5} y="30">АВАРІЯ</text></>}
          {current.series.map((series: Row, seriesIndex: number) => series.style === "bar" ? visible.map((sample: Row, index: number) => {
            const value = valueAt(sample, series), width = Math.max(2, 760 / Math.max(visible.length, 1) / Math.max(current.series.length, 1));
            if (!Number.isFinite(value)) return null;
            const valueY = y(value, series, seriesIndex);
            const laneBottom = 24 + (162 / Math.max(1, current.series.length)) * (seriesIndex + 1) - 5;
            return <rect key={`${seriesIndex}-${index}`} x={x(sample) - width / 2 + seriesIndex * 2} y={valueY} width={width} height={Math.max(1, laneBottom - valueY)} fill={series.color} opacity=".42"><title>{clock(sample.model_time)} · {value} {metricSpec(series)?.unit}</title></rect>;
          }) : <path key={seriesIndex} d={linePath(series, seriesIndex)} fill="none" stroke={series.color} strokeWidth="3" strokeDasharray={diagnosticSeriesDash(seriesIndex)} strokeLinecap="round" strokeLinejoin="round"><title>{metricSpec(series)?.label}</title></path>)}
          <text x="48" y="210">{clock(visible[0]?.model_time)}</text><text x="872" y="210" textAnchor="end">{clock(visible.at(-1)?.model_time)}</text>
        </svg>}
      </div>
      <div className="chart-zoom"><span>Масштаб часу</span><input aria-label="Початок ділянки" type="range" min="0" max="99" step="0.1" value={fromPct} onChange={(e) => setFromPct(Math.min(Number(e.target.value), toPct - .1))}/><input aria-label="Кінець ділянки" type="range" min="1" max="100" step="0.1" value={toPct} onChange={(e) => setToPct(Math.max(Number(e.target.value), fromPct + .1))}/><button onClick={focusIncident}>Аварія ±12 год</button><button onClick={() => { setFromPct(0); setToPct(100); }}>Весь час</button></div>
    </div>
  );
}

function PaneTitle({ small, title }: { small: string; title: string }) {
  return (
    <div className="pane-title">
      <small>{small}</small>
      <b>{title}</b>
    </div>
  );
}
function WorkflowElementForm({ node, save, readOnly }: { node: Row; save: (value: Row) => void; readOnly: boolean }) {
  const [name, setName] = useState(node.name || ""),
    [blockType, setBlockType] = useState(node.block_type || "component_command"),
    [eventType, setEventType] = useState(node.config?.event_type || "");
  useEffect(() => {
    setName(node.name || "");
    setBlockType(node.block_type || "component_command");
    setEventType(node.config?.event_type || "");
  }, [node.id, node.name, node.block_type, node.config?.event_type]);
  const kinds = [
    ["start", "Початкова подія"], ["event_trigger", "Проміжна подія"], ["timer", "Таймер"],
    ["condition", "Виключний шлюз"], ["parallel_split", "Паралельний шлюз"], ["join", "Об’єднання"],
    ["component_command", "Сервісна задача"], ["manual_command", "Ручна задача"], ["python_action", "Скриптова задача"],
    ["metric_check", "Бізнес-правило"], ["resource_operation", "Операція з ресурсом"], ["wait", "Очікування"],
    ["success", "Кінцева подія"], ["failure", "Кінцева подія з помилкою"],
  ];
  return <div className="workflow-element-form">
    <small>BPMN 2.0 · ВЛАСТИВОСТІ ЕЛЕМЕНТА</small>
    <label><span>Назва</span><input readOnly={readOnly} value={name} onChange={(event) => setName(event.target.value)} /></label>
    <label><span>Тип BPMN</span><select disabled={readOnly || node.block_type === "start"} value={blockType} onChange={(event) => setBlockType(event.target.value)}>{kinds.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
    <label><span>Подія / дія</span><input readOnly={readOnly} value={eventType} onChange={(event) => setEventType(event.target.value)} placeholder="event.type" /></label>
    {!readOnly && <button onClick={() => save({ name, block_type: blockType, config: { ...(node.config || {}), event_type: eventType } })}>Зберегти властивості</button>}
    <details><summary>Розширені параметри</summary><JsonEditor readOnly={readOnly} label="JSON" value={node.config || {}} save={(config) => save({ config })} /></details>
  </div>;
}
function JsonEditor({
  label,
  value,
  save,
  readOnly = false,
}: {
  label: string;
  value: any;
  save: (v: any) => void;
  readOnly?: boolean;
}) {
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  useEffect(() => setText(JSON.stringify(value, null, 2)), [value]);
  return (
    <label className="editor">
      <span>{label}</span>
      <textarea
        readOnly={readOnly}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      {!readOnly && (
        <button
          onClick={() => {
            try {
              save(JSON.parse(text));
            } catch {
              alert("Некоректний JSON");
            }
          }}
        >
          Зберегти
        </button>
      )}
    </label>
  );
}
function ParameterForm({ schema, value, save, readOnly }: { schema: Row; value: Row; save: (value: Row) => void; readOnly: boolean }) {
  const [draft, setDraft] = useState<Row>(value);
  useEffect(() => setDraft(value), [value]);
  const fields = [...new Set([...Object.keys(schema || {}), ...Object.keys(value || {})])];
  return <div className="parameter-form"><small>СТРУКТУРОВАНІ ПАРАМЕТРИ</small>{fields.map((key) => {
    const current = draft[key] ?? schema[key], type = typeof current;
    if (type === "boolean") return <label key={key}><span>{key.replaceAll("_", " ")}</span><input type="checkbox" checked={Boolean(current)} disabled={readOnly} onChange={(event) => setDraft({ ...draft, [key]: event.target.checked })} /></label>;
    if (type === "number") return <label key={key}><span>{key.replaceAll("_", " ")}</span><input type="number" value={current ?? ""} disabled={readOnly} onChange={(event) => setDraft({ ...draft, [key]: Number(event.target.value) })} /></label>;
    if (type === "string") return <label key={key}><span>{key.replaceAll("_", " ")}</span><input value={current ?? ""} disabled={readOnly} onChange={(event) => setDraft({ ...draft, [key]: event.target.value })} /></label>;
    return null;
  })}{!readOnly && <button onClick={() => save(draft)}>Зберегти параметри</button>}<details><summary>Розширений JSON</summary><pre>{JSON.stringify(draft, null, 2)}</pre></details></div>;
}
function CodeEditor({
  value,
  save,
  readOnly = false,
}: {
  value: string;
  save: (v: string) => void;
  readOnly?: boolean;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <label className="editor code">
      <span>Локальна поведінка Python · advanced</span>
      <textarea
        readOnly={readOnly}
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      {!readOnly && <button onClick={() => save(text)}>Зберегти Python</button>}
    </label>
  );
}
