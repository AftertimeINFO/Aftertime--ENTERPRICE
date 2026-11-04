import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Row } from "./api";
import { isActive, runStatusLabel } from "./e2eRun";
import { ScreenWorkspace, streamsOf } from "./ScreenWorkspace";
import { formatOffset, replayBounds } from "./replay";
import { STATUS_LABELS, formatDuration } from "./reports";
import { launchTitle, needsSummary, progressView } from "./launch";

type Props = {
  onOpenLaunch: (launchId: string) => void;
  onError: (message: string) => void;
  /** Replay a finished run of a BPMN process on the process itself. */
  onReplayProcess?: (binding: string, runId: string) => void;
};

/** Launches suites, groups or cases of the E2E control center from Aftertime and follows them. */
export function LaunchPanel({ onOpenLaunch, onError, onReplayProcess }: Props) {
  const [center, setCenter] = useState<Row | null>(null);
  const [runs, setRuns] = useState<Row[]>([]);
  const [current, setCurrent] = useState<Row | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [dryRun, setDryRun] = useState(false);
  const [stopOnFailure, setStopOnFailure] = useState(false);
  const next = useRef(0);
  const currentId = useRef<string | null>(null);
  // A finished run picked for replay; otherwise the active (or latest) run is shown.
  const [viewId, setViewId] = useState<string | null>(null);
  const viewRef = useRef<string | null>(null);
  viewRef.current = viewId;

  const refresh = useCallback(async () => {
    const [state, list] = await Promise.all([api.centerState(), api.centerRuns()]);
    setCenter(state);
    setRuns(list);
    const active = list.find((item) => item.id === viewRef.current) || list.find(isActive) || list[0];
    if (!active) return;
    if (active.id !== currentId.current) {
      currentId.current = active.id;
      next.current = 0;
      setLines([]);
    }
    const detail = await api.e2eRun(active.id, next.current);
    next.current = detail.next;
    setCurrent(detail);
    if (detail.lines.length) setLines((old) => [...old, ...detail.lines].slice(-400));
  }, []);
  useEffect(() => { refresh().catch(() => {}); }, [viewId, refresh]);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    const loop = async () => {
      try { await refresh(); } catch (error) { if (alive) onError(String(error)); }
      if (alive) timer = window.setTimeout(loop, 3000);
    };
    loop();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [refresh, onError]);

  const start = async (body: Row) => {
    try {
      await api.startCenterRun({ ...body, dryRun, stopOnFailure });
      setSelected([]);
      await refresh();
    } catch (error) {
      onError(String(error));
    }
  };
  const stop = async () => {
    if (!current) return;
    try { await api.stopE2eRun(current.id); await refresh(); } catch (error) { onError(String(error)); }
  };
  // A finished run is replayed with every recorded window on one clock.
  const [replayT, setReplayT] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(10);
  const finished = !!current && !isActive(current);
  const bounds = finished ? replayBounds(current, []) : null;
  useEffect(() => { setReplayT(null); setPlaying(false); }, [current?.id]);
  useEffect(() => {
    // "Програти" in the history opens the run and starts the replay at once.
    if (viewId && current?.id === viewId && bounds) { setReplayT(bounds.start); setPlaying(true); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [viewId, current?.id]);
  useEffect(() => {
    if (!playing || !bounds) return;
    const timer = window.setInterval(() => setReplayT((t) => {
      const value = Math.min(bounds.end, (t ?? bounds.start) + 250 * speed);
      if (value >= bounds.end) setPlaying(false);
      return value;
    }), 250);
    return () => window.clearInterval(timer);
  }, [playing, speed, bounds?.start, bounds?.end]);

  const toggleCase = (id: string) => setSelected((old) => old.includes(id) ? old.filter((item) => item !== id) : [...old, id]);

  const data = center?.data || {};
  const catalog = data.catalog || { suites: [], groups: {} };
  const health = data.health || {};
  const busy = runs.some(isActive);
  const progress = progressView(current?.progress);
  return <div className="launch">
    <section className="card launch-status">
      <h3>Стенд і міст</h3>
      <div className="launch-badges">
        <span className={center?.online ? "ok" : "bad"}>{center?.online ? "Міст на зв'язку" : "Міст не на зв'язку"}</span>
        {data.error ? <span className="bad">Центр E2E недоступний</span> : <span className="ok">Центр E2E працює</span>}
        {[["manager", "Менеджер тестів"], ["terminal", "Термінал"], ["prro", "ПРРО"], ["scanner", "Сканер"]].map(([key, title]) =>
          key in health && <span key={key} className={health[key] ? "ok" : "off"}>{title}</span>)}
        {health.freeD != null && <span className={health.freeD < 5 ? "bad" : "off"}>D: {health.freeD} ГБ</span>}
      </div>
      <span className="muted">
        {data.running ? `У центрі виконується ${data.running}` : "Центр вільний"}
        {data.queue?.length ? ` · у черзі ${data.queue.length}` : ""}
        {center?.received_at ? ` · оновлено ${new Date(center.received_at).toLocaleTimeString("uk-UA")}` : ""}
      </span>
    </section>

    <section className="card launch-current">
      <header>
        <h3>Поточний запуск з Aftertime</h3>
        {current && isActive(current) && <button onClick={stop} disabled={current.stop_requested}>{current.stop_requested ? "Зупиняється…" : "Зупинити"}</button>}
      </header>
      {current ? <>
        <b>{launchTitle(current)}</b>
        <span className={`e2e-status ${current.status}`}>{runStatusLabel(current)}</span>
        {progress && <>
          <div className="progress"><span style={{ width: `${progress.pct}%` }} /></div>
          <span className="muted">{progress.text}</span>
          <ol className="launch-suites">{(current.progress?.suites || []).map((suite: Row) =>
            <li key={suite.id} className={suite.state}>{suite.title} <small>{suite.state}</small></li>)}</ol>
        </>}
        {viewId && <button className="link" onClick={() => setViewId(null)}>← до поточного запуску</button>}
        {bounds && <div className="replay-controls" aria-label="Відтворення всіх вікон прогону">
          <button title={playing ? "Пауза" : "Програти всі вікна разом"} onClick={() => {
            if (!playing && (replayT == null || replayT >= bounds.end)) setReplayT(bounds.start);
            setPlaying(!playing);
          }}>{playing ? "⏸" : "▶"}</button>
          <select value={speed} title="Швидкість відтворення" onChange={(event) => setSpeed(Number(event.target.value))}>
            {[1, 5, 10, 30, 60].map((value) => <option key={value} value={value}>{value}×</option>)}
          </select>
          <input type="range" aria-label="Мить прогону" min={bounds.start} max={bounds.end} step={500} value={replayT ?? bounds.start}
            onChange={(event) => setReplayT(Number(event.target.value))} />
          <span>{formatOffset(replayT ?? bounds.start, bounds)} / {formatOffset(bounds.end, bounds)}</span>
          {replayT != null && <button title="Вийти з відтворення" onClick={() => { setReplayT(null); setPlaying(false); }}>✕</button>}
        </div>}
        <div className="launch-screens">
          <ScreenWorkspace runId={current.id} active={isActive(current)} streams={streamsOf(current)} at={replayT} place="launches" initial="tile" />
        </div>
        {lines.length > 0 && <pre className="e2e-log">{lines.slice(-60).join("\n")}</pre>}
        {!!current.report_launch_ids?.length && <div className="links">{current.report_launch_ids.map((id: string) =>
          <button key={id} className="link" onClick={() => onOpenLaunch(id)}>Звіт запуску</button>)}</div>}
      </> : <span className="muted">З Aftertime ще нічого не запускали</span>}
    </section>

    <section className="card launch-catalog">
      <header>
        <h3>Набори й кейси</h3>
        <label><input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} /> пробний (без 1С)</label>
        <label><input type="checkbox" checked={stopOnFailure} onChange={(event) => setStopOnFailure(event.target.checked)} /> зупинитись на збої</label>
        <button disabled={busy || !selected.length || !center?.online} onClick={() => start({ cases: selected, title: `${selected.length} кейс(ів)` })}>
          Запустити вибрані ({selected.length})
        </button>
      </header>
      {!center?.online && <span className="muted">Запуск можливий, коли міст на зв'язку: python -m e2e_bridge serve</span>}
      <div className="launch-groups">{Object.entries(catalog.groups || {}).map(([group, suites]) =>
        <button key={group} disabled={busy || !center?.online} title={(suites as string[]).join(", ")} onClick={() => start({ suites: [group], title: `група ${group}` })}>
          Група «{group}»
        </button>)}</div>
      <ul className="launch-suite-list">{(catalog.suites || []).map((suite: Row) => <li key={suite.id}>
        <details>
          <summary>
            <span><b>{suite.id}</b> {suite.title}</span>
            <small>{needsSummary(suite.needs)}</small>
            <button disabled={busy || !center?.online} onClick={(event) => { event.preventDefault(); start({ suites: [suite.id], title: suite.id }); }}>Запустити набір</button>
          </summary>
          <ul>{(suite.cases || []).map((item: Row) => <li key={item.id}>
            <label>
              <input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggleCase(item.id)} />
              <i className={`dot ${item.last}`} title={STATUS_LABELS[item.last] || "не запускався"} />
              <span>{item.title}</span>
              <small>{item.id}{item.duration ? ` · ${formatDuration(item.duration * 1000)}` : ""}</small>
            </label>
          </li>)}</ul>
        </details>
      </li>)}</ul>
    </section>

    <section className="card launch-history">
      <h3>Запуски з Aftertime</h3>
      <table><tbody>{runs.map((run) => <tr key={run.id}>
        <td>{new Date(run.created_at).toLocaleString("uk-UA")}</td>
        <td>{launchTitle(run)}</td>
        <td className={`e2e-status ${run.status}`}>{runStatusLabel(run)}</td>
        <td>{!isActive(run) && <button title="Програти виконаний тест" onClick={() => {
          if (run.kind === "binding" && run.binding && onReplayProcess) onReplayProcess(run.binding, run.id);
          else setViewId(run.id);
        }}>▶ Програти</button>}</td>
      </tr>)}</tbody></table>
      {!!data.history?.length && <>
        <h3>Історія центру E2E</h3>
        <table><tbody>{data.history.slice(0, 15).map((job: Row) => <tr key={job.id}>
          <td>{job.started || job.id}</td><td>{job.title}</td><td className={`e2e-status ${job.status}`}>{job.status}</td>
        </tr>)}</tbody></table>
      </>}
    </section>
  </div>;
}
