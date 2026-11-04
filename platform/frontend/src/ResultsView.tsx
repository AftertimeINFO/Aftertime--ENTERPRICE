import { useEffect, useMemo, useState } from "react";
import { api, type Row } from "./api";
import { ReportsView } from "./ReportsView";
import { STATUS_VIEW, percent, relativeTime } from "./testsCatalog";
import { EmptyState, Kpi, StatusBadge, StatusBar, Trend } from "./ui";

type Props = {
  tab: string | null;
  onTab: (tab: string) => void;
  onOpenTest: (id: string) => void;
  reportLaunchId: string | null;
  onOpenProcess: (binding: string, replayRunId?: string) => void;
  onError: (message: string) => void;
};

/** Consolidated results of the whole suite: where it stands, what needs attention, how it moves. */
export function ResultsView({ tab, onTab, onOpenTest, reportLaunchId, onOpenProcess, onError }: Props) {
  const current = tab || (reportLaunchId ? "reports" : "summary");
  return <div className="ux results-view">
    <header className="ux-header">
      <div>
        <h2>Результати</h2>
        <p>Останній результат кожного тесту набору, стан за наборами й історія запусків</p>
      </div>
      <nav className="ux-tabs" role="tablist">
        {([["summary", "Зведення"], ["matrix", "Матриця запусків"], ["reports", "Детальні звіти"]] as [string, string][]).map(([key, title]) =>
          <button key={key} role="tab" aria-selected={current === key} className={current === key ? "active" : ""} onClick={() => onTab(key)}>{title}</button>)}
      </nav>
    </header>
    {current === "summary" && <Summary onOpenTest={onOpenTest} onError={onError} onTab={onTab} />}
    {current === "matrix" && <Matrix onOpenTest={onOpenTest} onError={onError} />}
    {current === "reports" && <ReportsView initialLaunchId={reportLaunchId} onOpenProcess={onOpenProcess} onError={onError} />}
  </div>;
}

function Summary({ onOpenTest, onError, onTab }: { onOpenTest: (id: string) => void; onError: (message: string) => void; onTab: (tab: string) => void }) {
  const [data, setData] = useState<Row | null>(null);
  useEffect(() => { api.resultsSummary().then(setData).catch((error) => onError(String(error))); }, [onError]);
  if (!data) return <EmptyState title="Збираю зведення…" />;
  if (!data.tests) return <EmptyState title="Набір тестів ще порожній">Каталог з'явиться, коли міст до стенда передасть його в Aftertime.</EmptyState>;
  const failing = (data.statuses.failed || 0) + (data.statuses.broken || 0);
  const maxDay = Math.max(1, ...data.daily.map((day: Row) => Object.entries(day).filter(([key]) => key !== "day").reduce((sum, [, value]) => sum + Number(value), 0)));
  return <div className="results-summary">
    <section className="kpis">
      <Kpi label="Тестів у наборі" value={data.tests} hint={`${data.suites.length} наборів`} />
      <Kpi label="Успішність" value={percent(data.pass_rate)} hint={`за останнім результатом ${data.executed} тестів`} tone={data.pass_rate >= 0.95 ? "good" : data.pass_rate >= 0.8 ? "warn" : "bad"} />
      <Kpi label="Зараз падають" value={failing} hint="збій або зламано" tone={failing ? "bad" : "good"} />
      <Kpi label="Нестабільні" value={data.flaky} hint="змінювали результат за 10 запусків" tone={data.flaky ? "warn" : "good"} />
      <Kpi label="Не запускались" value={data.statuses.never || 0} />
      <Kpi label="Останній запуск" value={relativeTime(data.last_launch)} hint={data.last_launch ? new Date(data.last_launch).toLocaleString("uk-UA") : ""} />
    </section>

    <section className="panel">
      <h3>Потребують уваги</h3>
      {data.attention.length ? <table className="ux-table">
        <thead><tr><th>Статус</th><th>Тест</th><th>Останні 10</th><th>Стабільність</th><th>Причина</th></tr></thead>
        <tbody>{data.attention.map((test: Row) => <tr key={test.id} onClick={() => onOpenTest(test.id)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && onOpenTest(test.id)}>
          <td><StatusBadge status={test.status} /></td>
          <td><b>{test.title}</b><small className="block">{test.id}</small></td>
          <td><Trend statuses={test.trend} /></td>
          <td>{percent(test.pass_rate)}{test.flaky && <em className="tag warn">нестабільний</em>}</td>
          <td className="clip" title={test.last?.message || ""}>{test.last?.message || test.last?.category || ""}</td>
        </tr>)}</tbody>
      </table> : <EmptyState title="Усе зелене">Немає тестів зі збоєм чи нестабільних.</EmptyState>}
    </section>

    <section className="panel">
      <h3>За наборами</h3>
      <table className="ux-table">
        <thead><tr><th>Набір</th><th>Тестів</th><th>Стан</th><th>Пройдено</th><th>Нестабільні</th><th>Останній запуск</th></tr></thead>
        <tbody>{[...data.suites].sort((left: Row, right: Row) => String(left.id).localeCompare(String(right.id))).map((suite: Row) => <tr key={suite.id}>
          <td><b>{suite.id}</b><small className="block clip" title={suite.title}>{suite.title}</small></td>
          <td>{suite.tests}</td>
          <td className="wide"><StatusBar counts={suite.statuses} /></td>
          <td>{suite.statuses.passed || 0} / {suite.tests}</td>
          <td>{suite.flaky || "—"}</td>
          <td>{relativeTime(suite.last_run)}</td>
        </tr>)}</tbody>
      </table>
      <button className="link" onClick={() => onTab("matrix")}>Подивитися матрицю запусків →</button>
    </section>

    <section className="panel">
      <h3>Результати за днями</h3>
      {data.daily.length ? <div className="daily" role="img" aria-label="Кількість результатів за днями з розподілом за статусами">
        {data.daily.map((day: Row) => {
          const total = Object.entries(day).filter(([key]) => key !== "day").reduce((sum, [, value]) => sum + Number(value), 0);
          return <div key={day.day} className="daily-day" title={`${day.day}: ${Object.entries(day).filter(([key]) => key !== "day").map(([key, value]) => `${STATUS_VIEW[key]?.label || key} ${value}`).join(", ")}`}>
            <div className="daily-bar" style={{ height: `${(total / maxDay) * 100}%` }}>
              {["failed", "broken", "skipped", "passed"].filter((key) => day[key]).map((key) => <i key={key} className={key} style={{ flexGrow: day[key] }} />)}
            </div>
            <small>{day.day.slice(5)}</small>
          </div>;
        })}
      </div> : <EmptyState title="За останні 14 днів запусків не було" />}
    </section>
  </div>;
}

function Matrix({ onOpenTest, onError }: { onOpenTest: (id: string) => void; onError: (message: string) => void }) {
  const [suite, setSuite] = useState("");
  const [data, setData] = useState<Row | null>(null);
  const [suites, setSuites] = useState<Row[]>([]);
  useEffect(() => { api.resultsSummary().then((summary) => setSuites(summary.suites)).catch(() => {}); }, []);
  useEffect(() => { setData(null); api.resultsMatrix(suite).then(setData).catch((error) => onError(String(error))); }, [suite, onError]);
  const launches: Row[] = data?.launches || [];
  const rows = useMemo(() => (data?.tests || []).filter((test: Row) => Object.keys(test.cells).length), [data]);
  return <section className="panel matrix-panel">
    <div className="ux-toolbar">
      <select aria-label="Набір" value={suite} onChange={(event) => setSuite(event.target.value)}>
        <option value="">Усі набори</option>
        {[...suites].sort((left, right) => String(left.id).localeCompare(String(right.id))).map((item) => <option key={item.id} value={item.id}>{item.id} — {item.title}</option>)}
      </select>
      <span className="muted">Рядки — тести, стовпці — останні {launches.length} запусків (новіші праворуч). Клік відкриває тест.</span>
    </div>
    {!data ? <EmptyState title="Завантаження…" /> : !rows.length ? <EmptyState title="Для цього набору результатів ще немає" /> :
      <div className="matrix-scroll">
        <table className="matrix">
          <thead><tr><th className="sticky">Тест</th>{launches.map((launch) =>
            <th key={launch.id} title={`${launch.name} · ${launch.start ? new Date(launch.start).toLocaleString("uk-UA") : ""}`}>
              <span>{launch.start ? new Date(launch.start).toLocaleDateString("uk-UA", { day: "2-digit", month: "2-digit" }) : launch.name}</span>
            </th>)}</tr></thead>
          <tbody>{rows.map((test: Row) => <tr key={test.id} onClick={() => onOpenTest(test.id)}>
            <th className="sticky" title={test.id}>{test.title}</th>
            {launches.map((launch) => {
              const cell = test.cells[launch.id];
              return <td key={launch.id} className={cell ? `cell ${cell.status}` : "cell none"} title={cell ? `${STATUS_VIEW[cell.status]?.label || cell.status} · ${launch.name}` : "не входив у запуск"}>
                {cell ? STATUS_VIEW[cell.status]?.icon || "?" : ""}
              </td>;
            })}
          </tr>)}</tbody>
        </table>
      </div>}
  </section>;
}
