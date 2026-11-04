import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api, type Row } from "./api";
import { formatDuration } from "./reports";
import { LaunchPanel } from "./LaunchPanel";
import { TestDetail } from "./ReportsView";
import { isActive, runStatusLabel } from "./e2eRun";
import {
  STATUS_VIEW, filterTests, percent, relativeTime, routeHash, sortTests, suitesOf, type TestFilters, type TestSort,
} from "./testsCatalog";
import { EmptyState, StatusBadge, Trend } from "./ui";

type Props = {
  selectedId: string | null;
  tab: string | null;
  onSelect: (id: string | null) => void;
  onTab: (tab: string) => void;
  onOpenProcess: (binding: string, replayRunId?: string) => void;
  onOpenLaunch: (launchId: string) => void;
  onError: (message: string) => void;
};

const SORTS: [TestSort, string][] = [["attention", "Спершу проблемні"], ["name", "За назвою"], ["recent", "Нещодавні"], ["stability", "Найменш стабільні"]];

/** The whole end-to-end test suite: browse, filter, look into a test, run what is needed. */
export function TestsView({ selectedId, tab, onSelect, onTab, onOpenProcess, onOpenLaunch, onError }: Props) {
  const [tests, setTests] = useState<Row[] | null>(null);
  const [retired, setRetired] = useState(false);
  const [filters, setFilters] = useState<TestFilters>({ query: "", suite: "", statuses: [], flakyOnly: false });
  const [sort, setSort] = useState<TestSort>("attention");
  const [chosen, setChosen] = useState<string[]>([]);
  const [stand, setStand] = useState<{ online: boolean; busy: boolean }>({ online: false, busy: false });
  const [notice, setNotice] = useState("");
  const list = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async () => {
    try {
      const [items, center, runs] = await Promise.all([api.tests(retired), api.centerState(), api.centerRuns()]);
      setTests(items);
      setStand({ online: !!center.online, busy: runs.some(isActive) });
    } catch (error) {
      onError(String(error));
    }
  }, [retired, onError]);
  useEffect(() => {
    load();
    const timer = window.setInterval(load, 30_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const visible = useMemo(() => sortTests(filterTests(tests || [], filters), sort), [tests, filters, sort]);
  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    for (const test of tests || []) result[test.status] = (result[test.status] || 0) + 1;
    return result;
  }, [tests]);
  const suites = useMemo(() => suitesOf(tests || []), [tests]);

  const run = async (cases: string[], title: string) => {
    try {
      await api.startCenterRun({ cases, title });
      setNotice(`«${title}» поставлено в чергу стенда`);
      setChosen([]);
      onTab("launch");
    } catch (error) {
      onError(String(error));
    }
  };
  const runBlocked = !stand.online ? "Міст до стенда не на зв'язку" : stand.busy ? "На стенді вже йде запуск з Aftertime" : "";

  const moveSelection = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const index = visible.findIndex((test) => test.id === selectedId);
    const next = visible[Math.max(0, Math.min(visible.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)))];
    if (next) {
      onSelect(next.id);
      list.current?.querySelector<HTMLElement>(`[data-test="${CSS.escape(next.id)}"]`)?.scrollIntoView({ block: "nearest" });
    }
  };
  const toggleStatus = (status: string) => setFilters((current) => ({
    ...current, statuses: current.statuses.includes(status) ? current.statuses.filter((item) => item !== status) : [...current.statuses, status],
  }));
  const allChosen = visible.length > 0 && visible.every((test) => chosen.includes(test.id));

  return <div className="ux tests-view">
    <header className="ux-header">
      <div>
        <h2>Тести</h2>
        <p>{tests ? `${tests.length} тестів у наборі · ${suites.length} наборів` : "Завантаження…"}{runBlocked ? ` · ${runBlocked}` : ""}</p>
      </div>
      <nav className="ux-tabs" role="tablist">
        <button role="tab" aria-selected={tab !== "launch"} className={tab !== "launch" ? "active" : ""} onClick={() => onTab("list")}>Перелік</button>
        <button role="tab" aria-selected={tab === "launch"} className={tab === "launch" ? "active" : ""} onClick={() => onTab("launch")}>Запуск і черга</button>
      </nav>
    </header>
    {notice && <div className="ux-notice" role="status">{notice}<button className="link" onClick={() => setNotice("")}>закрити</button></div>}

    {tab === "launch" ? <LaunchPanel onError={onError} onOpenLaunch={onOpenLaunch} onReplayProcess={(binding, runId) => onOpenProcess(binding, runId)} /> : <>
      <div className="ux-toolbar">
        <input type="search" aria-label="Пошук тестів" placeholder="Пошук: назва, ідентифікатор, текст помилки" value={filters.query}
          onChange={(event) => setFilters({ ...filters, query: event.target.value })} />
        <select aria-label="Набір" value={filters.suite} onChange={(event) => setFilters({ ...filters, suite: event.target.value })}>
          <option value="">Усі набори</option>
          {suites.map((suite) => <option key={suite.id} value={suite.id}>{suite.id} — {suite.title}</option>)}
        </select>
        <select aria-label="Сортування" value={sort} onChange={(event) => setSort(event.target.value as TestSort)}>
          {SORTS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <div className="ux-chips" role="group" aria-label="Статус">
          {Object.keys(STATUS_VIEW).filter((status) => counts[status]).map((status) =>
            <button key={status} aria-pressed={filters.statuses.includes(status)} className={`chip ${status} ${filters.statuses.includes(status) ? "on" : ""}`} onClick={() => toggleStatus(status)}>
              {STATUS_VIEW[status].icon} {STATUS_VIEW[status].label} <b>{counts[status]}</b>
            </button>)}
          <button aria-pressed={filters.flakyOnly} className={`chip flaky ${filters.flakyOnly ? "on" : ""}`} onClick={() => setFilters({ ...filters, flakyOnly: !filters.flakyOnly })}>
            ≈ Нестабільні <b>{(tests || []).filter((test) => test.flaky).length}</b>
          </button>
        </div>
        <label className="ux-check"><input type="checkbox" checked={retired} onChange={(event) => setRetired(event.target.checked)} /> вилучені з набору</label>
        <span className="ux-spacer" />
        <button className="primary" disabled={!chosen.length || !!runBlocked} title={runBlocked || undefined}
          onClick={() => run(chosen, `${chosen.length} тест(ів) з Aftertime`)}>▶ Запустити вибрані ({chosen.length})</button>
      </div>

      <div className={`tests-body ${selectedId ? "with-detail" : ""}`}>
        <div className="tests-table" ref={list} tabIndex={0} onKeyDown={moveSelection} aria-label="Перелік тестів; стрілки вгору й вниз перемикають тест">
          <div className="tests-row head" role="row">
            <span><input type="checkbox" aria-label="Вибрати всі показані" checked={allChosen}
              onChange={() => setChosen(allChosen ? [] : visible.map((test) => test.id))} /></span>
            <span>Статус</span><span>Тест</span><span>Останній запуск</span><span>Останні 10</span><span>Стабільність</span>
          </div>
          {tests === null ? <EmptyState title="Завантаження тестів…" />
            : !visible.length ? <EmptyState title="Немає тестів за цими умовами">Змініть пошук або фільтри.</EmptyState>
            : visible.map((test) => <div key={test.id} data-test={test.id} role="row"
              className={`tests-row ${test.id === selectedId ? "selected" : ""} ${test.active ? "" : "retired"}`}
              onClick={() => onSelect(test.id === selectedId ? null : test.id)}>
              <span onClick={(event) => event.stopPropagation()}>
                <input type="checkbox" aria-label={`Вибрати ${test.title}`} checked={chosen.includes(test.id)}
                  onChange={() => setChosen((current) => current.includes(test.id) ? current.filter((id) => id !== test.id) : [...current, test.id])} />
              </span>
              <span><StatusBadge status={test.status} /></span>
              <span className="tests-title">
                <b>{test.title}</b>
                <small>{test.id}{test.suites?.length ? ` · ${test.suites.map((suite: Row) => suite.id).join(", ")}` : ""}{test.active ? "" : " · вилучено з набору"}</small>
                {test.binding && <em className="tag" title="Тест прив'язано до процесу BPMN">BPMN</em>}
              </span>
              <span>{relativeTime(test.last?.start)}{test.last ? <small>{formatDuration(test.last.duration_ms)}</small> : null}</span>
              <span><Trend statuses={test.trend} /></span>
              <span>{percent(test.pass_rate)}{test.flaky && <em className="tag warn" title="Результат змінювався кілька разів за останні запуски">нестабільний</em>}</span>
            </div>)}
        </div>
        {selectedId && <TestCard id={selectedId} runBlocked={runBlocked} onClose={() => onSelect(null)} onRun={run}
          onOpenProcess={onOpenProcess} onError={onError} />}
      </div>
    </>}
  </div>;
}

function TestCard({ id, runBlocked, onClose, onRun, onOpenProcess, onError }: {
  id: string; runBlocked: string; onClose: () => void; onRun: (cases: string[], title: string) => void;
  onOpenProcess: (binding: string, replayRunId?: string) => void; onError: (message: string) => void;
}) {
  const [test, setTest] = useState<Row | null>(null);
  const [view, setView] = useState<"result" | "history" | "runs">("result");
  const [resultId, setResultId] = useState<string | null>(null);
  useEffect(() => {
    setTest(null);
    setView("result");
    api.test(id).then((item) => { setTest(item); setResultId(item.last?.id || null); }).catch((error) => onError(String(error)));
  }, [id, onError]);

  if (!test) return <aside className="test-card"><EmptyState title="Завантаження…" /></aside>;
  return <aside className="test-card" aria-label={`Тест ${test.title}`}>
    <header>
      <StatusBadge status={test.status} />
      <div>
        <h3>{test.title}</h3>
        <small>{test.id} · {(test.suites || []).map((suite: Row) => `${suite.id} — ${suite.title}`).join("; ")}</small>
      </div>
      <button className="icon" aria-label="Закрити картку тесту" onClick={onClose}>✕</button>
    </header>
    <div className="test-card-actions">
      <button className="primary" disabled={!!runBlocked || !test.active} title={runBlocked || (!test.active ? "Тест вилучено з набору" : undefined)}
        onClick={() => onRun([test.id], test.title)}>▶ Запустити тест</button>
      {test.binding && <button onClick={() => onOpenProcess(test.binding)}>Відкрити процес BPMN</button>}
      <button title="Кроки, які стенд виконує перед цим тестом" onClick={() => { window.location.hash = routeHash("prep", test.id); }}>Підготовка перед тестом</button>
    </div>
    <dl className="test-facts">
      <div><dt>Останній запуск</dt><dd>{relativeTime(test.last?.start)}</dd></div>
      <div><dt>Тривалість</dt><dd>{test.last ? formatDuration(test.last.duration_ms) : "—"}</dd></div>
      <div><dt>Стабільність</dt><dd>{percent(test.pass_rate)} з {test.runs} {test.flaky ? "· нестабільний" : ""}</dd></div>
      <div><dt>Останні 10</dt><dd><Trend statuses={test.trend} /></dd></div>
    </dl>
    {test.last?.message && test.status !== "passed" && <pre className="test-failure">{test.last.message}</pre>}
    <nav className="ux-tabs small" role="tablist">
      <button role="tab" aria-selected={view === "result"} className={view === "result" ? "active" : ""} onClick={() => setView("result")}>Кроки результату</button>
      <button role="tab" aria-selected={view === "history"} className={view === "history" ? "active" : ""} onClick={() => setView("history")}>Історія ({test.history.length})</button>
      <button role="tab" aria-selected={view === "runs"} className={view === "runs" ? "active" : ""} onClick={() => setView("runs")}>Прогони з Aftertime ({test.aftertime_runs?.length || 0})</button>
    </nav>
    {view === "result" && (resultId ? <TestDetail id={resultId} onSelect={setResultId} onOpenProcess={(binding) => onOpenProcess(binding)} onError={onError} onArchived={() => {}} />
      : <EmptyState title="Тест ще не запускався">Запустіть його, щоб побачити кроки й вкладення.</EmptyState>)}
    {view === "history" && (test.history.length ? <table className="ux-table">
      <thead><tr><th>Коли</th><th>Статус</th><th>Тривалість</th><th>Запуск</th><th>Повідомлення</th></tr></thead>
      <tbody>{test.history.map((item: Row) => <tr key={item.id} className={item.id === resultId ? "selected" : ""} onClick={() => { setResultId(item.id); setView("result"); }}>
        <td>{item.start ? new Date(item.start).toLocaleString("uk-UA") : "—"}</td>
        <td><StatusBadge status={item.status} /></td>
        <td>{formatDuration(item.duration_ms)}</td>
        <td>{item.launch}</td>
        <td className="clip" title={item.message || ""}>{item.message || ""}</td>
      </tr>)}</tbody>
    </table> : <EmptyState title="Історії ще немає" />)}
    {view === "runs" && (test.aftertime_runs?.length ? <table className="ux-table">
      <thead><tr><th>Коли</th><th>Як запущено</th><th>Стан</th><th>Відео</th><th /></tr></thead>
      <tbody>{test.aftertime_runs.map((item: Row) => <tr key={item.id}>
        <td>{new Date(item.created_at).toLocaleString("uk-UA")}</td>
        <td>{item.kind === "binding" ? `процес ${item.binding}` : item.target?.title || "набір"}</td>
        <td>{runStatusLabel(item)}</td>
        <td>{item.recording === "ready" ? "є" : item.recording === "packing" ? "пакується" : "—"}</td>
        <td>{item.kind === "binding" && !isActive(item) && <button onClick={() => onOpenProcess(item.binding, item.id)}>▶ Програти</button>}</td>
      </tr>)}</tbody>
    </table> : <EmptyState title="З Aftertime цей тест ще не запускали" />)}
  </aside>;
}
