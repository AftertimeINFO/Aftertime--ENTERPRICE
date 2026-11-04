import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Row } from "./api";
import { streamsOf, type ScreenStream } from "./ScreenWorkspace";
import { VmScreen } from "./VmScreen";
import { E2E_MARKERS, elementMarkers, isActive, phaseSummary, runStatusLabel } from "./e2eRun";
import { formatOffset, phaseMoments, replayBounds, replayStates, type Bounds } from "./replay";
import { PrepProgress } from "./PrepProgress";

export type ReplayRequest = { runId: string; nonce: number };
export type ScreenView = { runId: string; active: boolean; focusPhase: string | null; at: number | null; streams: ScreenStream[] };
type Replay = { t: number; playing: boolean; speed: number; frames: Row[]; bounds: Bounds };
const SPEEDS = [1, 5, 10, 30, 60];

type Props = {
  workflow: Row;
  viewer: any;
  onError: (message: string) => void;
  onOpenReport?: (launchId: string) => void;
  /** Shows the machine screen of the selected run next to the diagram (null hides it). */
  onScreen?: (screen: ScreenView | null) => void;
  /** Open this run and replay it straight away (from the list of launches). */
  replayRequest?: ReplayRequest | null;
};

/** Runs the end-to-end test bound to a published process and paints its progress on the diagram. */
export function E2ePanel({ workflow, viewer, onError, onOpenReport, onScreen, replayRequest }: Props) {
  const versionId = workflow.version.id;
  const [runs, setRuns] = useState<Row[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [run, setRun] = useState<Row | undefined>();
  const [lines, setLines] = useState<string[]>([]);
  const [focusPhase, setFocusPhase] = useState<string | null>(null);
  const [replay, setReplay] = useState<Replay | null>(null);
  // Results of the bound test from reports (runs of a whole suite included), painted on demand.
  const [reportResults, setReportResults] = useState<Row[]>([]);
  const [reportView, setReportView] = useState<Row | null>(null);
  useEffect(() => {
    setReportView(null);
    api.workflowReportResults(versionId).then(setReportResults).catch(() => setReportResults([]));
  }, [versionId, run?.status]);
  const pendingReplay = useRef<string | null>(replayRequest?.runId || null);
  const runId = run?.id;
  const runActive = isActive(run);
  const replayAt = replay ? replay.t : null;
  useEffect(() => {
    onScreen?.(runId ? { runId, active: runActive, focusPhase, at: replayAt, streams: streamsOf(run) } : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onScreen, runId, runActive, focusPhase, replayAt, JSON.stringify(run?.streams || []), run?.recording?.status]);
  useEffect(() => () => onScreen?.(null), [onScreen]);
  const next = useRef(0);
  const painted = useRef<string[]>([]);

  const refreshList = useCallback(async () => {
    const list = await api.e2eRuns(versionId);
    setRuns(list);
    const wanted = pendingReplay.current && list.some((item) => item.id === pendingReplay.current) ? pendingReplay.current : null;
    setSelectedId((current) => wanted || current || list[0]?.id || null);
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
      // Keep polling while the run goes on, and after it while its screen video is being packed.
      if (isActive(current) || current.recording?.status === "packing") timer = window.setTimeout(() => poll().catch(() => {}), isActive(current) ? 2000 : 5000);
      else refreshList().catch(() => {});
    };
    let timer = 0;
    poll().catch((error) => onError(String(error)));
    return () => { alive = false; window.clearTimeout(timer); };
  }, [selectedId, refreshList, onError]);

  // While replaying, the run is shown as it was at the replayed moment; a picked report result instead of the run.
  const shown = reportView ? { states: reportView.states, status: reportView.status === "broken" ? "failed" : reportView.status }
    : run && replay ? {
    ...run,
    states: replayStates(run, replay.frames, replay.t, replay.bounds),
    status: replay.t >= replay.bounds.end ? run.status : "running",
  } : run;

  const startReplay = useCallback(async (target: Row) => {
    try {
      const frames = await api.e2eFrames(target.id);
      const bounds = replayBounds(target, frames);
      if (!bounds) { onError("Для цього прогону немає часових даних для відтворення"); return; }
      setReplay({ t: bounds.start, playing: true, speed: 10, frames, bounds });
    } catch (error) {
      onError(String(error));
    }
  }, [onError]);

  useEffect(() => {
    // Opened from a list of launches: replay as soon as the run is loaded.
    if (run && pendingReplay.current === run.id && !isActive(run)) {
      pendingReplay.current = null;
      startReplay(run);
    }
  }, [run, startReplay]);

  useEffect(() => {
    if (!replay?.playing) return;
    const timer = window.setInterval(() => setReplay((current) => {
      if (!current) return current;
      const t = Math.min(current.bounds.end, current.t + 250 * current.speed);
      return { ...current, t, playing: t < current.bounds.end };
    }), 250);
    return () => window.clearInterval(timer);
  }, [replay?.playing]);

  useEffect(() => { setReplay(null); setReportView(null); }, [selectedId]);
  useEffect(() => {
    if (!replayRequest) return;
    pendingReplay.current = replayRequest.runId;
    if (run?.id === replayRequest.runId && !isActive(run)) { pendingReplay.current = null; startReplay(run); }
    else setSelectedId(replayRequest.runId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [replayRequest?.nonce]);

  // The slider moves in 0.5 s steps and may stop just short of the end: snap to it then.
  const seek = (t: number) => setReplay((current) => current && {
    ...current,
    t: current.bounds.end - t < 600 ? current.bounds.end : Math.max(current.bounds.start, t),
  });
  const moments = replay && run ? phaseMoments(run, replay.frames) : [];
  const jumpToPhase = (phase: string) => {
    const moment = moments.find((item) => item.phase === phase);
    if (moment) seek(moment.t);
  };
  const nextPhase = () => {
    if (!replay) return;
    const moment = moments.find((item) => item.t > replay.t + 500);
    seek(moment ? moment.t : replay.bounds.end);
  };
  const togglePlay = () => setReplay((current) => {
    if (!current) return current;
    if (current.playing) return { ...current, playing: false };
    return { ...current, playing: true, t: current.t >= current.bounds.end ? current.bounds.start : current.t };
  });

  useEffect(() => {
    if (!viewer) return;
    const canvas = viewer.get("canvas");
    const registry = viewer.get("elementRegistry");
    // Another process may be open by now: only its own elements can lose their markers.
    for (const id of painted.current.filter((item) => registry.get(item))) for (const marker of E2E_MARKERS) canvas.removeMarker(id, marker);
    const markers = elementMarkers(shown, workflow.nodes);
    painted.current = Object.keys(markers).filter((id) => registry.get(id));
    for (const id of painted.current) canvas.addMarker(id, markers[id]);
  }, [viewer, shown, workflow.nodes]);

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
    <div className={`e2e-status ${shown?.status || "none"}`}>{replay ? "Відтворення · " : reportView ? "Зі звіту · " : ""}{runStatusLabel(shown)}{shown ? ` · ${phaseSummary(shown)}` : ""}</div>
    {reportView && <div className="report-view-note">
      <span>Результат {new Date(reportView.start).toLocaleString("uk-UA")}{reportView.message ? ` — ${reportView.message.slice(0, 160)}` : ""}</span>
      {onOpenReport && <button className="link" onClick={() => onOpenReport(reportView.launch.id)}>Звіт</button>}
      <button className="link" onClick={() => setReportView(null)}>✕ до прогону</button>
    </div>}
    {replay && <div className="replay-controls">
      <button title="На початок" onClick={() => seek(replay.bounds.start)}>⏮</button>
      <button title={replay.playing ? "Пауза" : "Програти"} onClick={togglePlay}>{replay.playing ? "⏸" : "▶"}</button>
      <button title="Наступна фаза" onClick={nextPhase}>⏭</button>
      <select value={replay.speed} title="Швидкість відтворення" onChange={(event) => setReplay({ ...replay, speed: Number(event.target.value) })}>
        {SPEEDS.map((speed) => <option key={speed} value={speed}>{speed}×</option>)}
      </select>
      <input type="range" min={replay.bounds.start} max={replay.bounds.end} step={500} value={replay.t} onChange={(event) => seek(Number(event.target.value))} />
      <span>{formatOffset(replay.t, replay.bounds)} / {formatOffset(replay.bounds.end, replay.bounds)}</span>
      <button title="Вийти з відтворення" onClick={() => setReplay(null)}>✕</button>
    </div>}
    {run && !reportView && <PrepProgress run={run} at={replay ? replay.t : null} onSkip={seek} />}
    {!!shown?.states?.length && <ol className="e2e-phases">
      {shown.states.map((item: Row) => <li key={item.phase} className={`e2e-${item.state}`} title={item.detail || item.evidence || (replay ? "Перейти до цієї фази" : "Показати екран VM у цій фазі")} onClick={() => replay ? jumpToPhase(item.phase) : setFocusPhase(item.phase)}>
        {item.phase}{item.inferred && item.state === "passed" ? " ·" : ""}
      </li>)}
    </ol>}
    {run && !onScreen && <VmScreen runId={run.id} active={isActive(run)} focusPhase={focusPhase} />}
    {lines.length > 0 && <pre className="e2e-log">{lines.slice(-40).join("\n")}</pre>}
    {run?.report_launch_id && onOpenReport && <button className="link" onClick={() => onOpenReport(run.report_launch_id)}>Відкрити звіт прогону</button>}
    {run?.folder && <span className="e2e-folder">Тека запуску: {run.folder}</span>}
    {runs.length > 0 && <ul className="e2e-runs">
      {runs.map((item) => <li key={item.id} className={item.id === selectedId ? "selected" : ""}>
        <button className="link" onClick={() => setSelectedId(item.id)}>{new Date(item.created_at).toLocaleString("uk-UA")}</button>
        <span className={`e2e-status ${item.status}`}>{runStatusLabel(item)}</span>
        {!isActive(item) && <button title="Програти виконаний тест: фази на BPMN і записані вікна в часі" onClick={() => {
          if (item.id === selectedId && run) startReplay(run);
          else { pendingReplay.current = item.id; setSelectedId(item.id); }
        }}>▶ Програти</button>}
      </li>)}
    </ul>}
    {reportResults.length > 0 && <details className="e2e-report-results" open={!runs.length}>
      <summary>Результати тесту зі звітів ({reportResults.length})</summary>
      <ul className="e2e-runs">
        {reportResults.map((item) => <li key={item.id} className={item.id === reportView?.id ? "selected" : ""}>
          <button className="link" title="Показати фази цього результату на схемі" onClick={() => { setReplay(null); setReportView(item); }}>
            {new Date(item.start).toLocaleString("uk-UA")}
          </button>
          <span className={`e2e-status ${item.status === "broken" ? "failed" : item.status}`}>{runStatusLabel({ status: item.status === "broken" ? "failed" : item.status })}</span>
          <small className="muted">{item.launch?.name}</small>
        </li>)}
      </ul>
    </details>}
  </aside>;
}
