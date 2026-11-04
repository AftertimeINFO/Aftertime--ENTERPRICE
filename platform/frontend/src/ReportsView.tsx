import { useEffect, useMemo, useState } from "react";
import { api, type Row } from "./api";
import { LaunchPanel } from "./LaunchPanel";
import {
  STATUS_LABELS, STATUSES, attachmentKind, formatDuration, stepCount, statusShares, suiteTree, tagsOf,
  type SuiteNode,
} from "./reports";

type Props = {
  initialLaunchId?: string | null;
  onOpenProcess: (binding: string, replayRunId?: string) => void;
  onError: (message: string) => void;
};

type Tab = "overview" | "suites" | "categories" | "launch";

/** Test reports in Aftertime: what Allure shows, read from imported allure-results. */
export function ReportsView({ initialLaunchId, onOpenProcess, onError }: Props) {
  const [launches, setLaunches] = useState<Row[]>([]);
  const [launchId, setLaunchId] = useState<string>(initialLaunchId || "");
  const [tab, setTab] = useState<Tab>(initialLaunchId ? "suites" : "overview");
  const [overview, setOverview] = useState<Row | null>(null);
  const [results, setResults] = useState<Row[]>([]);
  const [statusFilter, setStatusFilter] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [includeArchived, setIncludeArchived] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => { api.reportLaunches().then(setLaunches).catch((error) => onError(String(error))); }, [onError]);
  useEffect(() => { if (initialLaunchId) setLaunchId(initialLaunchId); }, [initialLaunchId]);
  useEffect(() => {
    api.reportOverview(launchId || undefined, includeArchived).then(setOverview).catch((error) => onError(String(error)));
    api.reportResults({ launch_id: launchId || undefined, include_archived: includeArchived ? "true" : undefined })
      .then(setResults).catch((error) => onError(String(error)));
  }, [launchId, includeArchived, revision, onError]);
  useEffect(() => setSelectedId(null), [launchId]);

  const visible = useMemo(() => results.filter((item) =>
    (!statusFilter.length || statusFilter.includes(item.status))
    && (!category || item.category === category)
    && (!query || `${item.name} ${item.full_name} ${item.message}`.toLowerCase().includes(query.toLowerCase())),
  ), [results, statusFilter, category, query]);

  const toggleStatus = (status: string) =>
    setStatusFilter((current) => current.includes(status) ? current.filter((item) => item !== status) : [...current, status]);

  return <div className="reports">
    <header className="reports-header">
      <div>
        <small>ЗВІТИ ТЕСТІВ</small>
        <h2>{overview?.launch ? overview.launch.name : "Останні результати всіх тестів"}</h2>
        <span>{overview ? `${overview.stats.total} тестів · ${overview.launches} запусків в історії` : "…"}</span>
      </div>
      <select value={launchId} onChange={(event) => setLaunchId(event.target.value)}>
        <option value="">Останні результати (кожен тест — найновіший)</option>
        {launches.map((launch) => <option key={launch.id} value={launch.id}>
          {launch.name} · {launch.stats?.passed ?? 0}/{launch.stats?.total ?? 0}
        </option>)}
      </select>
      <nav>
        {([["overview", "Огляд"], ["suites", "Набори"], ["categories", "Категорії"], ["launch", "Запуск"]] as [Tab, string][]).map(([key, title]) =>
          <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{title}</button>)}
      </nav>
    </header>

    {tab === "overview" && overview && <Overview overview={overview} onCategory={(name) => { setCategory(name); setTab("categories"); }} onLaunch={(id) => setLaunchId(id)} />}

    {tab === "launch" && <LaunchPanel onError={onError} onOpenLaunch={(id) => { setLaunchId(id); setTab("suites"); }}
      onReplayProcess={(binding, runId) => onOpenProcess(binding, runId)} />}

    {(tab === "suites" || tab === "categories") && <div className="reports-body">
      <section className="reports-list">
        <div className="reports-filters">
          {STATUSES.map((status) => <button key={status} className={`chip ${status} ${statusFilter.includes(status) ? "on" : ""}`} onClick={() => toggleStatus(status)}>
            {STATUS_LABELS[status]} {results.filter((item) => item.status === status).length}
          </button>)}
          <input placeholder="Пошук за назвою, кейсом, повідомленням" value={query} onChange={(event) => setQuery(event.target.value)} />
          {!launchId && <label className="archived-toggle"><input type="checkbox" checked={includeArchived} onChange={(event) => setIncludeArchived(event.target.checked)} /> архівні тести</label>}
        </div>
        {tab === "suites" ? <SuiteTree node={suiteTree(visible)} selectedId={selectedId} onSelect={setSelectedId} depth={0} />
          : <Categories overview={overview} results={visible} selected={category} onSelect={setCategory} selectedId={selectedId} onSelectTest={setSelectedId} />}
      </section>
      <section className="reports-detail">
        {selectedId ? <TestDetail id={selectedId} onSelect={setSelectedId} onOpenProcess={onOpenProcess} onError={onError} onArchived={() => setRevision((value) => value + 1)} />
          : <div className="reports-empty">Оберіть тест зі списку</div>}
      </section>
    </div>}
  </div>;
}

function StatusBar({ stats }: { stats: Row }) {
  return <div className="status-bar">{statusShares(stats).map((part) =>
    <span key={part.status} className={part.status} style={{ flexGrow: part.count }} title={`${STATUS_LABELS[part.status]}: ${part.count}`} />)}</div>;
}

function Overview({ overview, onCategory, onLaunch }: { overview: Row; onCategory: (name: string) => void; onLaunch: (id: string) => void }) {
  const total = overview.stats.total || 0;
  const passRate = total ? Math.round((overview.stats.passed / total) * 100) : 0;
  const trend: Row[] = overview.trend || [];
  const maxTotal = Math.max(1, ...trend.map((item) => item.stats?.total || 0));
  return <div className="reports-overview">
    <article className="card summary">
      <div className="ring" style={{ ["--rate" as string]: `${passRate}%` }}><b>{passRate}%</b><span>пройдено</span></div>
      <ul>{STATUSES.map((status) => <li key={status} className={status}><b>{overview.stats[status]}</b> {STATUS_LABELS[status]}</li>)}</ul>
      <StatusBar stats={overview.stats} />
      <span className="muted">Сумарна тривалість тестів: {formatDuration(overview.duration_ms)}</span>
    </article>
    <article className="card trend">
      <h3>Тренд за запусками</h3>
      <div className="trend-bars">{trend.map((item) => <button key={item.id} title={`${item.name}: ${item.stats?.passed ?? 0}/${item.stats?.total ?? 0}`}
        onClick={() => onLaunch(item.id)} style={{ height: `${Math.max(6, ((item.stats?.total || 0) / maxTotal) * 100)}%` }}>
        {STATUSES.map((status) => item.stats?.[status] ? <span key={status} className={status} style={{ flexGrow: item.stats[status] }} /> : null)}
      </button>)}</div>
      <span className="muted">Останні {trend.length} запусків; клік відкриває запуск</span>
    </article>
    <article className="card categories">
      <h3>Категорії збоїв</h3>
      {overview.categories.length ? <ul>{overview.categories.map((item: Row) =>
        <li key={item.name}><button onClick={() => onCategory(item.name)}>{item.name}</button><b>{item.count}</b></li>)}</ul>
        : <span className="muted">Збоїв немає</span>}
    </article>
    {overview.launch && <article className="card environment">
      <h3>Середовище</h3>
      <table><tbody>{Object.entries(overview.launch.environment || {}).map(([key, value]) =>
        <tr key={key}><th>{key}</th><td>{String(value)}</td></tr>)}</tbody></table>
      {overview.launch.executor?.name && <span className="muted">Виконавець: {overview.launch.executor.name} · {overview.launch.executor.buildName}</span>}
    </article>}
  </div>;
}

function SuiteTree({ node, selectedId, onSelect, depth }: { node: SuiteNode; selectedId: string | null; onSelect: (id: string) => void; depth: number }) {
  return <ul className={`suite-tree depth-${depth}`}>
    {node.children.map((child) => <li key={child.key}>
      <details open={depth < 1}>
        <summary><span>{child.title}</span><StatusCounts counts={child.counts} /></summary>
        <SuiteTree node={child} selectedId={selectedId} onSelect={onSelect} depth={depth + 1} />
      </details>
    </li>)}
    {node.tests.map((test) => <TestRow key={test.id} test={test} selected={test.id === selectedId} onSelect={onSelect} />)}
  </ul>;
}

function TestRow({ test, selected, onSelect }: { test: Row; selected: boolean; onSelect: (id: string) => void }) {
  return <li className={`test-row ${selected ? "selected" : ""}`} onClick={() => onSelect(test.id)}>
    <i className={`dot ${test.status}`} />
    <span>{test.name}</span>
    {test.binding && <em title="Прив'язано до процесу BPMN">BPMN</em>}
    {test.archived && <em className="archived" title="Архівний тест">архів</em>}
    <small>{formatDuration(test.duration_ms)}</small>
  </li>;
}

function StatusCounts({ counts }: { counts: Record<string, number> }) {
  return <span className="counts">{STATUSES.filter((status) => counts[status]).map((status) =>
    <b key={status} className={status}>{counts[status]}</b>)}</span>;
}

function Categories({ overview, results, selected, onSelect, selectedId, onSelectTest }: {
  overview: Row | null; results: Row[]; selected: string | null; onSelect: (name: string | null) => void;
  selectedId: string | null; onSelectTest: (id: string) => void;
}) {
  const names: string[] = (overview?.categories || []).map((item: Row) => item.name);
  return <ul className="suite-tree depth-0">
    {names.map((name) => {
      const tests = results.filter((item) => item.category === name);
      return <li key={name}>
        <details open={selected === name} onToggle={(event) => (event.target as HTMLDetailsElement).open && onSelect(name)}>
          <summary><span>{name}</span><span className="counts"><b className="failed">{tests.length}</b></span></summary>
          <ul className="suite-tree depth-1">{tests.map((test) =>
            <TestRow key={test.id} test={test} selected={test.id === selectedId} onSelect={onSelectTest} />)}</ul>
        </details>
      </li>;
    })}
    {selected && <li><button className="link" onClick={() => onSelect(null)}>Показати всі категорії</button></li>}
  </ul>;
}

export function TestDetail({ id, onSelect, onOpenProcess, onError, onArchived }: {
  id: string; onSelect: (id: string) => void; onOpenProcess: (binding: string) => void; onError: (message: string) => void; onArchived: () => void;
}) {
  const [test, setTest] = useState<Row | null>(null);
  useEffect(() => { setTest(null); api.reportResult(id).then(setTest).catch((error) => onError(String(error))); }, [id, onError]);
  const toggleArchive = async () => {
    if (!test) return;
    try {
      if (test.archived) await api.restoreTest(test.history_id);
      else await api.archiveTest(test.history_id, test.name);
      setTest({ ...test, archived: !test.archived });
      onArchived();
    } catch (error) {
      onError(String(error));
    }
  };
  if (!test) return <div className="reports-empty">Завантаження…</div>;
  const data = test.data || {};
  const details = data.statusDetails || {};
  const stored = new Set<string>(test.stored_attachments || []);
  return <article className="test-detail">
    <header>
      <i className={`dot ${test.status}`} />
      <div>
        <h3>{test.name}</h3>
        <span className="muted">{test.full_name} · {STATUS_LABELS[test.status]} · {formatDuration(test.duration_ms)} · {test.launch?.name}</span>
      </div>
      {test.binding && <button onClick={() => onOpenProcess(test.binding)}>Відкрити процес BPMN</button>}
      <button onClick={toggleArchive} title="Архівний тест не показується в останніх результатах; історія зберігається">
        {test.archived ? "Повернути з архіву" : "Архівувати"}
      </button>
    </header>
    {test.history?.length > 1 && <div className="history">
      <small>ІСТОРІЯ</small>
      {test.history.map((item: Row) => <button key={item.id} className={`dot ${item.status} ${item.id === test.id ? "current" : ""}`}
        title={`${item.launch} · ${STATUS_LABELS[item.status]} · ${formatDuration(item.duration_ms)}`} onClick={() => onSelect(item.id)} />)}
    </div>}
    {(details.message || details.trace) && <section className={`status-details ${test.status}`}>
      {details.message && <pre>{details.message}</pre>}
      {details.trace && <details><summary>Трасування</summary><pre>{details.trace}</pre></details>}
    </section>}
    {test.category && <span className="category">Категорія: {test.category}</span>}
    {!!data.parameters?.length && <table className="parameters"><tbody>{data.parameters.map((item: Row) =>
      <tr key={item.name}><th>{item.name}</th><td>{item.value}</td></tr>)}</tbody></table>}
    {!!tagsOf(data).length && <div className="tags">{tagsOf(data).map((tag) => <span key={tag}>{tag}</span>)}</div>}
    {!!data.links?.length && <div className="links">{data.links.map((link: Row) =>
      <a key={link.url} href={link.url} target="_blank" rel="noreferrer">{link.name || link.url}</a>)}</div>}
    {data.description && <p className="description">{data.description}</p>}
    <section className="steps">
      <small>КРОКИ · {stepCount(data)}</small>
      <Steps steps={data.steps || []} stored={stored} />
      <Attachments attachments={data.attachments || []} stored={stored} />
    </section>
  </article>;
}

function Steps({ steps, stored }: { steps: Row[]; stored: Set<string> }) {
  if (!steps.length) return null;
  return <ol className="step-list">{steps.map((step, index) => {
    const nested = !!(step.steps?.length || step.attachments?.length || step.statusDetails?.message);
    const title = <><i className={`dot ${step.status}`} /><span>{step.name}</span><small>{formatDuration((step.stop || 0) - (step.start || 0))}</small></>;
    return <li key={index}>
      {nested ? <details open={step.status !== "passed"}><summary>{title}</summary>
        {step.statusDetails?.message && <pre className="step-message">{step.statusDetails.message}</pre>}
        <Steps steps={step.steps || []} stored={stored} />
        <Attachments attachments={step.attachments || []} stored={stored} />
      </details> : <div className="step-line">{title}</div>}
    </li>;
  })}</ol>;
}

function Attachments({ attachments, stored }: { attachments: Row[]; stored: Set<string> }) {
  if (!attachments.length) return null;
  return <ul className="attachments">{attachments.map((item) => <Attachment key={item.source} item={item} available={stored.has(item.source)} />)}</ul>;
}

function Attachment({ item, available }: { item: Row; available: boolean }) {
  const [text, setText] = useState<string | null>(null);
  const url = api.reportAttachmentUrl(item.source);
  const kind = attachmentKind(item.type);
  if (!available) return <li className="attachment missing">📎 {item.name} · не імпортовано</li>;
  return <li className="attachment">
    <details onToggle={(event) => {
      if ((event.target as HTMLDetailsElement).open && kind === "text" && text === null) fetch(url).then((response) => response.text()).then(setText).catch(() => setText("Не вдалося прочитати"));
    }}>
      <summary>📎 {item.name} <small>{item.type}</small> <a href={url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>відкрити</a></summary>
      {kind === "image" && <img src={url} alt={item.name} loading="lazy" />}
      {kind === "video" && <video src={url} controls preload="metadata" />}
      {kind === "text" && <pre>{text ?? "…"}</pre>}
    </details>
  </li>;
}
