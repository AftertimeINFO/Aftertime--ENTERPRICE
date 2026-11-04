import type { Row } from "./api";

/**
 * Preparation of the test stand: the plan the stand runs before its tests (stages of steps), which
 * steps a given test gets, and what the stand reported about them during a run.
 *
 * The selection mirrors the stand's own reader of the plan: a step runs for a case when every
 * condition of ``when`` holds (true: the field is set; false: it is not; "x": equals; "!x": differs;
 * a list: one of), it is not switched off, and a ``suite`` step runs once per suite.
 */

export type PrepStep = Row & { id: string; title: string };
export type PrepStage = Row & { id: string; title: string; steps: PrepStep[]; fixed?: boolean };
export type Plan = Row & { stages: PrepStage[]; enabled?: boolean; vars?: Row };

export type Condition = { field: string; op: "set" | "unset" | "eq" | "ne"; value: string };

export function conditionsOf(when: Row | undefined): Condition[] {
  return Object.entries(when || {}).map(([field, want]) => {
    if (want === true) return { field, op: "set", value: "" };
    if (want === false) return { field, op: "unset", value: "" };
    const text = Array.isArray(want) ? want.join(", ") : String(want);
    return text.startsWith("!") ? { field, op: "ne", value: text.slice(1) } : { field, op: "eq", value: text };
  });
}

export function whenOf(conditions: Condition[]): Row | undefined {
  const when: Row = {};
  for (const item of conditions) {
    if (!item.field) continue;
    when[item.field] = item.op === "set" ? true : item.op === "unset" ? false : item.op === "ne" ? `!${item.value}` : item.value;
  }
  return Object.keys(when).length ? when : undefined;
}

/** Why a step does not run for a case, or null when it does. */
export function skipReason(step: PrepStep, testCase: Row): string | null {
  if (step.enabled === false) return "крок вимкнено";
  for (const [field, want] of Object.entries(step.when || {})) {
    const actual = testCase[field];
    const ok = want === true ? !!actual
      : want === false ? !actual
      : Array.isArray(want) ? want.map(String).includes(String(actual ?? ""))
      : String(want).startsWith("!") ? String(actual ?? "") !== String(want).slice(1)
      : String(actual ?? "") === String(want);
    if (!ok) return `лише коли ${describeCondition(conditionsOf({ [field]: want })[0])}`;
  }
  return null;
}

export function describeCondition(item: Condition): string {
  if (item.op === "set") return `${item.field} задано`;
  if (item.op === "unset") return `${item.field} не задано`;
  return `${item.field} ${item.op === "ne" ? "≠" : "="} ${item.value}`;
}

export function describeWhen(when: Row | undefined): string {
  const items = conditionsOf(when);
  return items.length ? items.map(describeCondition).join(" і ") : "завжди";
}

export const SCOPES: Record<string, string> = { case: "перед кожним тестом", suite: "раз на набір" };
export const RUNS: Record<string, string> = { group: "раз на групу тестів", case: "перед кожним тестом" };
/** ``suite-<variable>``: once per suite for every value of that variable of the plan. */
export const scopeLabel = (step: PrepStep) => SCOPES[step.scope || "case"]
  || (String(step.scope).startsWith("suite-") ? `раз на набір для кожного ${String(step.scope).slice(6)}` : step.scope);

/** Variables of the plan for a case, as the stand computes them. */
export function varsFor(plan: Plan, testCase: Row | null): Record<string, string> {
  const vars: Record<string, string> = {};
  if (!testCase) return vars;
  for (const [name, spec] of Object.entries<Row>(plan.vars || {})) {
    const raw = testCase[spec.from] == null ? "" : String(testCase[spec.from]);
    vars[name] = spec.map ? (raw && raw in spec.map ? String(spec.map[raw]) : String(spec.default ?? ""))
      : raw || String(spec.default ?? "");
  }
  return { ...vars, case: String(testCase.id) };
}

export function expand(text: string, vars: Record<string, string>): string {
  return text.replace(/\{(\w+)\}/g, (whole, name) => (name in vars ? vars[name] : whole));
}

/** Mean time of the steps that run for a case, from the stand's recent reports; null when nothing is known. */
export function estimateMs(plan: Plan, testCase: Row, stats: Row): number | null {
  let total = 0;
  let known = false;
  for (const stage of plan.stages) {
    if (stage.fixed) continue;
    for (const step of stage.steps) {
      if (skipReason(step, testCase)) continue;
      const mean = stats[step.id]?.mean_ms;
      if (mean != null) { total += mean; known = true; }
    }
  }
  return known ? total : null;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} мс`;
  const seconds = Math.round(ms / 1000);
  return seconds < 60 ? `${seconds} с` : `${Math.floor(seconds / 60)} хв ${seconds % 60} с`;
}

// ----- changes to the plan: every one returns a new plan -----

function mapStage(plan: Plan, stageId: string, change: (steps: PrepStep[]) => PrepStep[]): Plan {
  return { ...plan, stages: plan.stages.map((stage) => stage.id === stageId ? { ...stage, steps: change(stage.steps) } : stage) };
}

export function updateStep(plan: Plan, stageId: string, stepId: string, change: Partial<PrepStep>): Plan {
  return mapStage(plan, stageId, (steps) => steps.map((step) => {
    if (step.id !== stepId) return step;
    const next: PrepStep = { ...step, ...change };
    for (const key of Object.keys(next)) if (next[key] === undefined) delete next[key];
    return next;
  }));
}

export function moveStep(plan: Plan, stageId: string, stepId: string, by: -1 | 1): Plan {
  return mapStage(plan, stageId, (steps) => {
    const from = steps.findIndex((step) => step.id === stepId);
    const to = from + by;
    if (from < 0 || to < 0 || to >= steps.length) return steps;
    const next = [...steps];
    [next[from], next[to]] = [next[to], next[from]];
    return next;
  });
}

export function removeStep(plan: Plan, stageId: string, stepId: string): Plan {
  return mapStage(plan, stageId, (steps) => steps.filter((step) => step.id !== stepId));
}

export function addStep(plan: Plan, stageId: string): { plan: Plan; id: string } {
  const taken = new Set(plan.stages.flatMap((stage) => stage.steps.map((step) => step.id)));
  let number = 1;
  while (taken.has(`step-${number}`)) number += 1;
  const id = `step-${number}`;
  return { id, plan: mapStage(plan, stageId, (steps) => [...steps, { id, title: "Новий крок", script: "", args: ["-OutputPath", "{out}"] }]) };
}

/** A value on one line, spaced as the plan file is written: { "id": "x", "args": ["-Mode", "apply"] }. */
function inline(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(inline).join(", ")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value);
    return entries.length ? `{ ${entries.map(([key, item]) => `${JSON.stringify(key)}: ${inline(item)}`).join(", ")} }` : "{}";
  }
  return JSON.stringify(value);
}

/**
 * The plan as text, in the layout of the stand's file (a stage's keys one per line, every step and
 * variable on its own line), so that a change in the work area is a change of those lines only.
 */
export function planText(plan: Plan): string {
  const block = (entries: string[], indent: string, open: string, close: string) =>
    entries.length ? `${open}\n${entries.map((line) => `${indent}  ${line}`).join(",\n")}\n${indent}${close}` : `${open}${close}`;
  const stage = (item: PrepStage) => block(Object.entries(item).map(([key, value]) => key === "steps"
    ? `"steps": ${block((value as PrepStep[]).map(inline), "      ", "[", "]")}`
    : `${JSON.stringify(key)}: ${inline(value)}`), "    ", "{", "}");
  const top = Object.entries(plan).map(([key, value]) => {
    if (key === "stages") return `"stages": ${block((value as PrepStage[]).map(stage), "  ", "[", "]")}`;
    if (key === "vars" && value && typeof value === "object") {
      return `"vars": ${block(Object.entries(value).map(([name, spec]) => `${JSON.stringify(name)}: ${inline(spec)}`), "  ", "{", "}")}`;
    }
    return `${JSON.stringify(key)}: ${inline(value)}`;
  });
  return `${block(top, "", "{", "}")}\n`;
}

/** Lines removed and added between two texts (longest common subsequence), for "what will change". */
export function lineDiff(before: string, after: string): { kind: " " | "-" | "+"; text: string }[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const table = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out: { kind: " " | "-" | "+"; text: string }[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { out.push({ kind: " ", text: a[i] }); i += 1; j += 1; }
    else if (j < b.length && (i >= a.length || table[i][j + 1] > table[i + 1][j])) { out.push({ kind: "+", text: b[j] }); j += 1; }
    else { out.push({ kind: "-", text: a[i] }); i += 1; }
  }
  return out;
}

/** Changed lines with a little context around them; long unchanged runs fold into "…". */
export function diffExcerpt(lines: ReturnType<typeof lineDiff>, context = 2) {
  const keep = lines.map((line, index) => lines.slice(Math.max(0, index - context), index + context + 1).some((item) => item.kind !== " "));
  const out: (typeof lines[number] | null)[] = [];
  lines.forEach((line, index) => {
    if (keep[index]) out.push(line);
    else if (out[out.length - 1] !== null) out.push(null);
  });
  return out;
}

/** Line (0-based) where ``anchor`` first appears in a file, or -1. */
export function lineOf(text: string, anchor: string | undefined): number {
  if (!anchor) return -1;
  const at = text.indexOf(anchor);
  return at < 0 ? -1 : text.slice(0, at).split("\n").length - 1;
}

// ----- the preparation as a run reported it -----

const ms = (value: string | undefined) => (value ? new Date(value).getTime() : NaN);

export type PrepMoment = { n: number; total: number; title: string; state: string; step: string };

/** The preparation step on screen at moment ``t`` (ms) of a run: the last one reported until then. */
export function prepAt(timeline: Row[] | undefined, t: number): PrepMoment | null {
  let found: PrepMoment | null = null;
  for (const item of timeline || []) {
    if (item.prep && ms(item.t) <= t) found = { ...item.prep };
  }
  return found;
}

/** When the stand prepared itself (first report of the run to the first test phase) and how its steps ended. */
export function prepSummary(run: Row | null | undefined): { start: number; end: number | null; steps: Row[] } | null {
  const timeline: Row[] = run?.timeline || [];
  const preps = timeline.filter((item) => item.prep);
  const running = timeline.find((item) => item.status === "running");
  const firstPhase = timeline.find((item) => item.phase && item.state !== "pending");
  const start = ms(running?.t ?? preps[0]?.t);
  if (Number.isNaN(start)) return null;
  const done = new Map<string, Row>();
  for (const item of preps) if (item.prep.state !== "start") done.set(`${item.prep.n}:${item.prep.step}`, item.prep);
  return { start, end: firstPhase ? ms(firstPhase.t) : null, steps: [...done.values()] };
}
