import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { api, type Row } from "./api";
import {
  addStep, conditionsOf, describeCondition, describeWhen, diffExcerpt, estimateMs, expand, formatMs, lineDiff, lineOf, moveStep,
  planText, removeStep, RUNS, SCOPES, scopeLabel, skipReason, updateStep, varsFor, whenOf,
  type Condition, type Plan, type PrepStage, type PrepStep,
} from "./standPrep";
import { EmptyState } from "./ui";

type Props = { caseId: string | null; onCase: (id: string | null) => void; onError: (message: string) => void };
type OpenFile = { path: string; anchor?: string };

const LAST: Record<string, string> = { ok: "", failed: "помилка", skipped: "пропущено" };
const CHANGED: Record<string, string> = { changed: "змінено", unchanged: "без змін" };

/**
 * What the stand does before its tests, step by step: seen for every test or for one, edited here
 * (order, on/off, parameters, conditions) or straight in the file behind it.
 */
export function StandPrepView({ caseId, onCase, onError }: Props) {
  const [data, setData] = useState<Row | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [file, setFile] = useState<OpenFile | null>(null);
  const [review, setReview] = useState(false);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(() => api.standPreparation().then((value) => {
    setData(value);
    setPlan(value.plan || null);
    setReview(false);
  }).catch((error) => onError(String(error))), [onError]);
  useEffect(() => { load(); }, [load]);

  const original = useMemo(() => (data?.plan ? planText(data.plan) : ""), [data]);
  const current = plan ? planText(plan) : "";
  const dirty = !!plan && current !== original;
  const cases: Row[] = data?.cases || [];
  const testCase = cases.find((item) => item.id === caseId) || null;
  const vars = plan ? varsFor(plan, testCase) : {};
  const stats: Row = data?.stats || {};
  const locked = data?.busy ? "На стенді йде прогін: зміни можна зберегти після нього" : "";

  const save = async () => {
    if (!plan || !data) return;
    setSaving(true);
    try {
      await api.saveStandFile({ path: data.plan_path, text: current, sha: data.sha, note: note.trim() || "Зміни плану підготовки" });
      setNote("");
      setNotice("План підготовки збережено. Стенд візьме його на наступному прогоні.");
      await load();
    } catch (error) { onError(String(error)); } finally { setSaving(false); }
  };

  if (!data) return <div className="ux prep-view"><EmptyState title="Завантаження підготовки стенда…" /></div>;
  if (!data.configured) return <div className="ux prep-view"><EmptyState title="Підготовку стенда не налаштовано">
    Додайте e2e/preparation.json у теку налаштувань і змонтуйте каталог стенда (AFTERTIME_STAND_DIR).
  </EmptyState></div>;

  const change = (next: Plan) => setPlan(next);
  const estimate = plan && testCase ? estimateMs(plan, testCase, stats) : null;

  return <div className="ux prep-view">
    <header className="ux-header">
      <div>
        <h2>Підготовка стенда</h2>
        <p>Що стенд робить перед тестами, по кроках. План: <code>{data.plan_path}</code>
          {plan ? ` · ${plan.stages.reduce((sum, stage) => sum + stage.steps.length, 0)} кроків` : ""}</p>
      </div>
      <div className="prep-mode">
        <label className="prep-switch" title="Увімкнено: стенд виконує кроки за цим планом. Вимкнено: стенд працює по-старому, як записано в його коді.">
          <input type="checkbox" role="switch" checked={!!plan?.enabled} disabled={!plan}
            onChange={(event) => plan && change({ ...plan, enabled: event.target.checked })} />
          <span>{plan?.enabled ? "План увімкнено · стенд виконує кроки за ним" : "План вимкнено · стенд працює по-старому"}</span>
        </label>
      </div>
    </header>
    {notice && <div className="ux-notice" role="status">{notice}<button className="link" onClick={() => setNotice("")}>закрити</button></div>}
    {locked && <div className="ux-notice warn" role="status">{locked}</div>}
    {data.error && <div className="ux-notice warn" role="alert">{data.error} <button className="link" onClick={() => setFile({ path: data.plan_path })}>Відкрити файл плану</button></div>}

    <div className="ux-toolbar">
      <select aria-label="Для якого тесту показати кроки" value={caseId || ""} onChange={(event) => onCase(event.target.value || null)}>
        <option value="">Усі кроки плану</option>
        {cases.map((item) => <option key={item.id} value={item.id}>Для тесту: {item.title ? `${item.title.slice(0, 70)} · ` : ""}{item.id}</option>)}
      </select>
      {testCase && <span className="muted">Для цього тесту: {estimate != null ? `≈ ${formatMs(estimate)} за останніми прогонами` : "часу ще немає (план не виконувався)"}</span>}
      <span className="ux-spacer" />
      <button onClick={() => setFile({ path: data.plan_path })}>Файл плану</button>
      {dirty && <>
        <button onClick={() => { setPlan(data.plan); setReview(false); }}>Скасувати зміни</button>
        <button className="primary" onClick={() => setReview(true)}>Зберегти…</button>
      </>}
    </div>

    {review && plan && <section className="prep-review" aria-label="Що зміниться в плані">
      <h3>Що зміниться у файлі {data.plan_path}</h3>
      <DiffView before={data.text || original} after={current} />
      <div className="prep-review-actions">
        <input aria-label="Коментар до версії" placeholder="Що й навіщо змінено (видно в історії версій)" value={note} onChange={(event) => setNote(event.target.value)} />
        <button onClick={() => setReview(false)}>Назад до редагування</button>
        <button className="primary" disabled={saving || !!locked} title={locked || undefined} onClick={save}>{saving ? "Збереження…" : "Зберегти план"}</button>
      </div>
    </section>}

    {plan && <div className="prep-stages">
      {plan.stages.map((stage) => <StageView key={stage.id} stage={stage} plan={plan} testCase={testCase} vars={vars} stats={stats}
        editing={editing} onEditing={setEditing} onChange={change} onOpen={setFile} scripts={data.scripts} cases={cases} />)}
    </div>}

    {file && <StandFileEditor key={file.path + (file.anchor || "")} path={file.path} anchor={file.anchor} locked={locked}
      onClose={() => setFile(null)} onError={onError}
      onSaved={(path) => { if (path === data.plan_path) load(); setNotice(`Файл ${path} збережено.`); }} />}
  </div>;
}

function StageView({ stage, plan, testCase, vars, stats, editing, onEditing, onChange, onOpen, scripts, cases }: {
  stage: PrepStage; plan: Plan; testCase: Row | null; vars: Record<string, string>; stats: Row; editing: string | null;
  onEditing: (id: string | null) => void; onChange: (plan: Plan) => void; onOpen: (file: OpenFile) => void; scripts: string; cases: Row[];
}) {
  const rows = useRef<HTMLDivElement | null>(null);
  const focusRow = (id: string) => window.setTimeout(() => rows.current?.querySelector<HTMLElement>(`[data-step="${id}"]`)?.focus(), 0);
  const onKey = (event: KeyboardEvent, step: PrepStep) => {
    if (stage.fixed || !event.altKey || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) return;
    event.preventDefault();
    onChange(moveStep(plan, stage.id, step.id, event.key === "ArrowUp" ? -1 : 1));
    focusRow(step.id);
  };
  const scriptPath = (script: string) => (scripts ? `${scripts}/${script}` : script);
  const running = stage.steps.filter((step) => !testCase || stage.fixed || !skipReason(step, testCase));
  return <section className={`prep-stage ${stage.fixed ? "fixed" : ""}`} aria-label={stage.title}>
    <header>
      <h3>{stage.title}</h3>
      <span className="tag">{RUNS[stage.runs] || stage.runs}</span>
      {stage.session === "one-connection" && <span className="tag">одне підключення до бази</span>}
      {stage.fixed ? <span className="muted">виконується кодом стенда — змінюється у файлі
        {stage.file && <> <button className="link" onClick={() => onOpen({ path: stage.file })}>{stage.file}</button></>}</span>
        : <span className="muted">{testCase ? `${running.length} з ${stage.steps.length} кроків для цього тесту` : `${stage.steps.length} кроків · Alt+↑/↓ змінює порядок`}</span>}
    </header>
    <div className="prep-steps" ref={rows} role="list">
      {stage.steps.map((step, index) => {
        const reason = testCase && !stage.fixed ? skipReason(step, testCase) : step.enabled === false ? "крок вимкнено" : null;
        const stat = stats[step.id];
        const last = stat?.last;
        const open = editing === `${stage.id}/${step.id}`;
        return <div key={step.id} role="listitem" className={`prep-step ${reason ? "skipped" : ""} ${open ? "open" : ""}`}>
          <div className="prep-step-row" tabIndex={0} data-step={step.id} onKeyDown={(event) => onKey(event, step)}
            aria-label={`${index + 1}. ${expand(step.title, vars)}${reason ? `, не виконується: ${reason}` : ""}`}>
            {stage.fixed ? <span className="prep-num">{index + 1}</span>
              : <input type="checkbox" aria-label={`Виконувати крок «${step.title}»`} checked={step.enabled !== false}
                onChange={(event) => onChange(updateStep(plan, stage.id, step.id, { enabled: event.target.checked ? undefined : false }))} />}
            <span className="prep-title">
              <b>{expand(step.title, vars)}</b>
              <small>{stage.fixed ? `шукати в файлі: «${step.find}»`
                : `${step.script} ${(step.args || []).map((arg: string) => expand(arg, testCase ? { ...vars, folder: "‹тека прогону›", out: `‹тека прогону›/${testCase.id}-${step.id}.json` } : vars)).join(" ")}`}</small>
              {reason && <small className="prep-reason">не для {testCase ? "цього тесту" : "прогонів"}: {reason}</small>}
            </span>
            {!stage.fixed && <span className="prep-when" title="Коли крок виконується">
              <span className="tag">{scopeLabel(step)}</span>
              <small>{describeWhen(step.when)}{step.if ? ` · якщо ${step.if.field} ~ ${step.if.like}` : ""}{step.equip ? " · сервер обладнання" : ""}</small>
            </span>}
            {!stage.fixed && <span className="prep-time" title={stat ? `середнє за ${stat.runs} прогонів` : "план ще не виконувався"}>
              {formatMs(stat?.mean_ms)}
              {last && <small className={last.state}>{LAST[last.state] || ""}{last.changed ? CHANGED[last.changed] || last.changed : ""}</small>}
            </span>}
            <span className="prep-actions">
              {stage.fixed ? <button onClick={() => onOpen({ path: stage.file, anchor: step.find })}>Відкрити в файлі</button> : <>
                <button className="icon" aria-label="Вище" title="Вище (Alt+↑)" disabled={index === 0} onClick={() => onChange(moveStep(plan, stage.id, step.id, -1))}>↑</button>
                <button className="icon" aria-label="Нижче" title="Нижче (Alt+↓)" disabled={index === stage.steps.length - 1} onClick={() => onChange(moveStep(plan, stage.id, step.id, 1))}>↓</button>
                <button aria-expanded={open} onClick={() => onEditing(open ? null : `${stage.id}/${step.id}`)}>{open ? "Готово" : "Змінити"}</button>
                {step.script && <button onClick={() => onOpen({ path: scriptPath(step.script) })} title="Відкрити скрипт кроку">Скрипт</button>}
              </>}
            </span>
          </div>
          {open && <StepForm step={step} cases={cases} onChange={(change) => onChange(updateStep(plan, stage.id, step.id, change))}
            onRemove={() => { if (window.confirm(`Прибрати крок «${step.title}» з плану?`)) { onChange(removeStep(plan, stage.id, step.id)); onEditing(null); } }} />}
        </div>;
      })}
    </div>
    {!stage.fixed && <button className="link prep-add" onClick={() => {
      const added = addStep(plan, stage.id);
      onChange(added.plan);
      onEditing(`${stage.id}/${added.id}`);
    }}>＋ Додати крок</button>}
  </section>;
}

const NOT_CONDITIONS = ["id", "title", "feature", "phases", "expected", "group"];

function StepForm({ step, cases, onChange, onRemove }: { step: PrepStep; cases: Row[]; onChange: (change: Partial<PrepStep>) => void; onRemove: () => void }) {
  const conditions = conditionsOf(step.when);
  const setConditions = (next: Condition[]) => onChange({ when: whenOf(next) });
  // conditions are on the fields the stand's cases have
  const fields = [...new Set(cases.flatMap((item) => Object.keys(item)).filter((key) => !NOT_CONDITIONS.includes(key)))];
  return <div className="prep-form">
    <label>Назва<input value={step.title} onChange={(event) => onChange({ title: event.target.value })} /></label>
    <label>Скрипт<input value={step.script || ""} placeholder="setup-something.ps1" onChange={(event) => onChange({ script: event.target.value })} /></label>
    <label className="wide">Параметри (по одному в рядку; {"{out}"} — файл результату, {"{folder}"} — тека прогону, {"{case}"} і змінні плану)
      <textarea rows={Math.max(3, (step.args || []).length)} value={(step.args || []).join("\n")}
        onChange={(event) => onChange({ args: event.target.value.split("\n").map((line) => line.trim()).filter(Boolean) })} />
    </label>
    <label>Коли
      <select value={step.scope || "case"} onChange={(event) => onChange({ scope: event.target.value === "case" ? undefined : event.target.value })}>
        {Object.entries(SCOPES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        {step.scope && !(step.scope in SCOPES) && <option value={step.scope}>{scopeLabel(step)}</option>}
      </select>
    </label>
    <fieldset className="wide">
      <legend>Умови (усі разом)</legend>
      {conditions.map((item, index) => <div key={index} className="prep-condition">
        <select aria-label="Поле тесту" value={item.field} onChange={(event) => setConditions(conditions.map((old, at) => at === index ? { ...old, field: event.target.value } : old))}>
          {[...new Set([item.field, ...fields])].map((field) => <option key={field} value={field}>{field}</option>)}
        </select>
        <select aria-label="Умова" value={item.op} onChange={(event) => setConditions(conditions.map((old, at) => at === index ? { ...old, op: event.target.value as Condition["op"] } : old))}>
          <option value="set">задано</option><option value="unset">не задано</option><option value="eq">дорівнює</option><option value="ne">не дорівнює</option>
        </select>
        {(item.op === "eq" || item.op === "ne") && <input aria-label="Значення" value={item.value} onChange={(event) => setConditions(conditions.map((old, at) => at === index ? { ...old, value: event.target.value } : old))} />}
        <button className="icon" aria-label={`Прибрати умову ${describeCondition(item)}`} onClick={() => setConditions(conditions.filter((_, at) => at !== index))}>✕</button>
      </div>)}
      <button className="link" onClick={() => setConditions([...conditions, { field: fields[0] || "", op: "set", value: "" }])}>＋ умова</button>
      {!conditions.length && <small className="muted"> Без умов крок виконується для кожного тесту.</small>}
    </fieldset>
    {(step.if || step.equip || step.assertZero) && <p className="muted wide">
      {step.if && `Виконується, лише якщо в результаті кроку «${step.if.step}» поле ${step.if.field} відповідає «${step.if.like}». `}
      {step.equip && `Перед кроком стенд запускає сервер емуляції обладнання (${step.equip}). `}
      {step.assertZero && `Після кроку перевіряє, що ${step.assertZero.path} = 0, інакше зупиняє прогін. `}
      Ці правила змінюються у файлі плану.
    </p>}
    <div className="wide prep-form-actions"><button onClick={onRemove}>Прибрати крок з плану</button></div>
  </div>;
}

function DiffView({ before, after }: { before: string; after: string }) {
  const lines = diffExcerpt(lineDiff(before, after));
  return <pre className="prep-diff">{lines.map((line, index) => line === null
    ? <span key={index} className="fold">…</span>
    : <span key={index} className={line.kind === "+" ? "add" : line.kind === "-" ? "del" : ""}>{line.kind} {line.text}</span>)}</pre>;
}

/** A stand file opened in the work area: edited as text, saved with its encoding kept, with its saved versions at hand. */
export function StandFileEditor({ path, anchor, locked, onClose, onSaved, onError }: {
  path: string; anchor?: string; locked: string; onClose: () => void; onSaved: (path: string) => void; onError: (message: string) => void;
}) {
  const [file, setFile] = useState<Row | null>(null);
  const [text, setText] = useState("");
  const [note, setNote] = useState("");
  const [versions, setVersions] = useState<Row[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const area = useRef<HTMLTextAreaElement | null>(null);
  const dirty = !!file && text !== file.text;

  useEffect(() => {
    api.standFile(path).then((value) => { setFile(value); setText(value.text); }).catch((error) => setProblem(String(error)));
    api.standFileVersions(path).then(setVersions).catch(() => setVersions([]));
  }, [path]);
  useEffect(() => {
    // Opened at a step: its line is selected and scrolled into view.
    if (!file || !area.current) return;
    const line = lineOf(file.text, anchor);
    if (line < 0) return;
    const lines = file.text.split("\n");
    const start = lines.slice(0, line).join("\n").length + (line ? 1 : 0);
    area.current.focus();
    area.current.setSelectionRange(start, start + lines[line].length);
    area.current.scrollTop = Math.max(0, line * 18 - 120);
  }, [file, anchor]);

  const save = async () => {
    if (!file || !dirty) return;
    setSaving(true);
    setProblem("");
    try {
      const saved = await api.saveStandFile({ path, text, sha: file.sha, note: note.trim() || "Зміна з Aftertime" });
      setFile(saved);
      setText(saved.text);
      setNote("");
      setVersions(await api.standFileVersions(path));
      onSaved(path);
    } catch (error) { setProblem(String(error)); onError(String(error)); } finally { setSaving(false); }
  };
  const close = () => { if (!dirty || window.confirm("Закрити файл без збереження змін?")) onClose(); };
  const onKey = (event: KeyboardEvent) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
    if (event.key === "Escape") { event.preventDefault(); close(); }
  };

  return <section className="prep-file" aria-label={`Файл ${path}`} onKeyDown={onKey}>
    <header>
      <div>
        <h3>{path}</h3>
        <small className="muted">{file ? `${file.text.split("\n").length} рядків · ${file.bom ? "UTF-8 з BOM" : "UTF-8"} · ${file.eol === "crlf" ? "CRLF" : "LF"} — кодування й переноси рядків збережуться` : "Завантаження…"}</small>
      </div>
      <button className="icon" aria-label="Закрити файл" onClick={close}>✕</button>
    </header>
    {problem && <div className="ux-notice warn" role="alert">{problem}</div>}
    <div className="prep-file-body">
      <textarea ref={area} spellCheck={false} wrap="off" aria-label={`Вміст ${path}`} value={text} onChange={(event) => setText(event.target.value)} />
      <aside className="prep-versions" aria-label="Збережені версії">
        <b>Версії</b>
        {versions === null ? <small className="muted">Завантаження…</small>
          : !versions.length ? <small className="muted">Файл ще не змінювали з Aftertime.</small>
          : <ol>{versions.map((item) => <li key={item.id}>
            <span>{new Date(item.saved_at).toLocaleString("uk-UA")}</span>
            <small>{item.note}</small>
            <button className="link" onClick={async () => {
              try { setText((await api.standFileVersion(item.id)).text); } catch (error) { onError(String(error)); }
            }}>Взяти цю версію</button>
          </li>)}</ol>}
      </aside>
    </div>
    <footer>
      {dirty && <DiffView before={file?.text || ""} after={text} />}
      <div className="prep-review-actions">
        <input aria-label="Коментар до версії" placeholder="Що й навіщо змінено" value={note} onChange={(event) => setNote(event.target.value)} />
        {dirty && <button onClick={() => setText(file?.text || "")}>Скасувати зміни</button>}
        <button className="primary" disabled={!dirty || saving || !!locked} title={locked || "Ctrl+S"} onClick={save}>{saving ? "Збереження…" : "Зберегти файл"}</button>
      </div>
    </footer>
  </section>;
}
