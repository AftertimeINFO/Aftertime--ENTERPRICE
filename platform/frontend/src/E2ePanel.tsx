import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Row } from "./api";
import { VmScreen } from "./VmScreen";
import { E2E_MARKERS, elementMarkers, isActive, phaseSummary, runStatusLabel } from "./e2eRun";

type Props = {
  workflow: Row;
  viewer: any;
  onError: (message: string) => void;
  onOpenReport?: (launchId: string) => void;
  /** Shows the machine screen of the selected run next to the diagram (null hides it). */
  onScreen?: (screen: { runId: string; active: boolean; focusPhase: string | null } | null) => void;
};

/** Runs the end-to-end test bound to a published process and paints its progress on the diagram. */
export function E2ePanel({ workflow, viewer, onError, onOpenReport, onScreen }: Props) {
  const versionId = workflow.version.id;
  const [runs, setRuns] = useState<Row[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [run, setRun] = useState<Row | undefined>();
  const [lines, setLines] = useState<string[]>([]);
  const [focusPhase, setFocusPhase] = useState<string | null>(null);
  const runId = run?.id;
  const runActive = isActive(run);
  useEffect(() => {
    onScreen?.(runId ? { runId, active: runActive, focusPhase } : null);
  }, [onScreen, runId, runActive, focusPhase]);
  useEffect(() => () => onScreen?.(null), [onScreen]);
  const next = useRef(0);
  const painted = useRef<string[]>([]);

  const refreshList = useCallback(async () => {
    const list = await api.e2eRuns(versionId);
    setRuns(list);
    setSelectedId((current) => current || list[0]?.id || null);
  }, [versionId]);

  useEffect(() => {
    setRuns([]); setSelectedId(null); setRun(undefined); setLines([]); next.current = 0;
    refreshList().catch((error) => onError(String(error)));
  }, [refreshList, onError]);

  useEffect(() => {
    if (!selectedId) return;
    let alive = true;
    next.current = 0;
    setLines([]);
    const poll = async () => {
      const current = await api.e2eRun(selectedId, next.current);
      if (!alive) return;
      next.current = current.next;
      setRun(current);
      if (current.lines.length) setLines((old) => [...old, ...current.lines].slice(-300));
      if (isActive(current)) timer = window.setTimeout(() => poll().catch(() => {}), 2000);
      else refreshList().catch(() => {});
    };
    let timer = 0;
    poll().catch((error) => onError(String(error)));
    return () => { alive = false; window.clearTimeout(timer); };
  }, [selectedId, refreshList, onError]);

  useEffect(() => {
    if (!viewer) return;
    const canvas = viewer.get("canvas");
    const registry = viewer.get("elementRegistry");
    for (const id of painted.current) for (const marker of E2E_MARKERS) canvas.removeMarker(id, marker);
    const markers = elementMarkers(run, workflow.nodes);
    painted.current = Object.keys(markers).filter((id) => registry.get(id));
    for (const id of painted.current) canvas.addMarker(id, markers[id]);
  }, [viewer, run, workflow.nodes]);

  const start = async () => {
    try {
      const created = await api.startE2eRun(versionId);
      setRuns((old) => [created, ...old]);
      setSelectedId(created.id);
    } catch (error) {
      onError(String(error));
    }
  };
  const stop = async () => {
    if (!run) return;
    try { setRun({ ...run, ...(await api.stopE2eRun(run.id)) }); } catch (error) { onError(String(error)); }
  };

  const activeRun = runs.find(isActive);
  return <aside className="e2e-panel">
    <header>
      <small>E2E-ТЕСТ · {workflow.test.title}</small>
      {isActive(run) ? <button onClick={stop} disabled={run?.stop_requested}>{run?.stop_requested ? "Зупиняється…" : "Зупинити"}</button>
        : <button onClick={start} disabled={!workflow.test.covered || !!activeRun}>Запустити тест</button>}
    </header>
    <div className={`e2e-status ${run?.status || "none"}`}>{runStatusLabel(run)}{run ? ` · ${phaseSummary(run)}` : ""}</div>
    {!!run?.states?.length && <ol className="e2e-phases">
      {run.states.map((item: Row) => <li key={item.phase} className={`e2e-${item.state}`} title={item.detail || item.evidence || "Показати екран VM у цій фазі"} onClick={() => setFocusPhase(item.phase)}>
        {item.phase}{item.inferred && item.state === "passed" ? " ·" : ""}
      </li>)}
    </ol>}
    {run && !onScreen && <VmScreen runId={run.id} active={isActive(run)} focusPhase={focusPhase} />}
    {lines.length > 0 && <pre className="e2e-log">{lines.slice(-40).join("\n")}</pre>}
    {run?.report_launch_id && onOpenReport && <button className="link" onClick={() => onOpenReport(run.report_launch_id)}>Відкрити звіт прогону</button>}
    {run?.folder && <span className="e2e-folder">Тека запуску: {run.folder}</span>}
    {runs.length > 1 && <select value={selectedId || ""} onChange={(event) => setSelectedId(event.target.value)}>
      {runs.map((item) => <option key={item.id} value={item.id}>{new Date(item.created_at).toLocaleString("uk-UA")} · {runStatusLabel(item)}</option>)}
    </select>}
  </aside>;
}
