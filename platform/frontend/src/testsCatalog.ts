import type { Row } from "./api";

/** Statuses as people read them: an icon and a word, never colour alone. */
export const STATUS_VIEW: Record<string, { icon: string; label: string }> = {
  passed: { icon: "✓", label: "Пройдено" },
  failed: { icon: "✕", label: "Збій" },
  broken: { icon: "!", label: "Зламано" },
  skipped: { icon: "–", label: "Пропущено" },
  unknown: { icon: "?", label: "Невідомо" },
  never: { icon: "○", label: "Не запускався" },
};

export type TestFilters = { query: string; suite: string; statuses: string[]; flakyOnly: boolean };
export type TestSort = "attention" | "name" | "passed" | "recent" | "duration" | "trend" | "stability";
/** A sort and its direction: ``reversed`` turns the natural order of the sort around (env_ai_dev#24). */
export type SortState = { sort: TestSort; reversed: boolean };

const ATTENTION: Record<string, number> = { failed: 0, broken: 1, unknown: 3, skipped: 4, never: 5, passed: 6 };

export function filterTests(tests: Row[], filters: TestFilters): Row[] {
  const query = filters.query.trim().toLowerCase();
  return tests.filter((test) =>
    (!query || `${test.id} ${test.title} ${test.last?.message || ""}`.toLowerCase().includes(query))
    && (!filters.suite || (test.suites || []).some((suite: Row) => suite.id === filters.suite))
    && (!filters.statuses.length || filters.statuses.includes(test.status))
    && (!filters.flakyOnly || test.flaky));
}

/** Share of the recent runs (``trend``) that did not pass; a test without runs counts as none failed. */
export function trendFailures(test: Row): number {
  const trend: string[] = test.trend || [];
  return trend.length ? trend.filter((status) => status !== "passed").length / trend.length : -1;
}

/**
 * "attention" puts what needs a look first: failures, then flaky tests, then the rest.
 * Natural orders: name A-Я, the latest run first, the longest run first, the most failures among the
 * recent runs first, the least stable first. ``reversed`` turns the order around; ties stay by name.
 */
export function sortTests(tests: Row[], sort: TestSort, reversed = false): Row[] {
  const byName = (left: Row, right: Row) => String(left.title).localeCompare(String(right.title), "uk");
  const started = (test: Row) => (test.last?.start ? Date.parse(test.last.start) : 0);
  const passedAt = (test: Row) => (test.last_passed?.start ? Date.parse(test.last_passed.start) : 0);
  const lasted = (test: Row) => (typeof test.last?.duration_ms === "number" ? test.last.duration_ms : -1);
  const key: (left: Row, right: Row) => number =
    sort === "name" ? () => 0
    : sort === "recent" ? (left, right) => started(right) - started(left)
    // env_ai_dev#29: the latest successful run first; never passed - last
    : sort === "passed" ? (left, right) => passedAt(right) - passedAt(left)
    : sort === "duration" ? (left, right) => lasted(right) - lasted(left)
    : sort === "trend" ? (left, right) => trendFailures(right) - trendFailures(left)
    : sort === "stability" ? (left, right) => (left.pass_rate ?? 2) - (right.pass_rate ?? 2)
    : (left, right) => (ATTENTION[left.status] ?? 9) - (ATTENTION[right.status] ?? 9) || Number(right.flaky) - Number(left.flaky);
  const direction = reversed ? -1 : 1;
  return [...tests].sort((left, right) => direction * key(left, right) || direction * byName(left, right));
}

/** A click on the header of a column: the same column turns its order around, another one starts in its natural order. */
export function nextSort(current: SortState, sort: TestSort): SortState {
  return current.sort === sort ? { sort, reversed: !current.reversed } : { sort, reversed: false };
}

export function suitesOf(tests: Row[]): Row[] {
  const seen = new Map<string, Row>();
  for (const test of tests) for (const suite of test.suites || []) if (!seen.has(suite.id)) seen.set(suite.id, suite);
  return [...seen.values()].sort((left, right) => String(left.id).localeCompare(String(right.id)));
}

export function relativeTime(value: string | null | undefined, now: number = Date.now()): string {
  if (!value) return "ніколи";
  const minutes = Math.round((now - Date.parse(value)) / 60_000);
  if (minutes < 1) return "щойно";
  if (minutes < 60) return `${minutes} хв тому`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} год тому`;
  const days = Math.round(hours / 24);
  return days < 30 ? `${days} дн тому` : new Date(value).toLocaleDateString("uk-UA");
}

export function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

/** Address of a view in the browser, so a test or a tab can be linked and survives a reload. */
export function parseRoute(hash: string): { section: string; id: string | null; tab: string | null } {
  const [section = "", second = "", third = ""] = hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
  if (section === "results") return { section, id: null, tab: second || null };
  return { section, id: second || null, tab: third || null };
}

export function routeHash(section: string, id?: string | null, tab?: string | null): string {
  const parts = [section, id, tab].filter(Boolean).map((part) => encodeURIComponent(part as string));
  return `#/${parts.join("/")}`;
}

/** A summary block of a version group used as a filter of the table (env_ai_dev#29). */
export type VersionKind = "executed" | "passed" | "failed" | "missing";
export type VersionPick = { group: "chosen" | "latest"; kind: VersionKind };

/** A line of the version picker: the version and its result in short (env_ai_dev#29). */
export function versionOptionLabel(item: Row, marks: { best?: boolean; latest?: boolean } = {}): string {
  const tags = [marks.best ? "найкраща" : "", marks.latest ? "остання" : ""].filter(Boolean).join(", ");
  const members: string[] = item.members || [];
  const grouped = members.length > 1 ? ` (${members.length} збірки)` : "";
  return `${item.version}${grouped} · ✓ ${item.passed} · ✗ ${item.failed} · виконано ${item.executed}/${item.tests} (${item.percent}%)${tags ? ` · ${tags}` : ""}`;
}
export const VERSION_KIND_LABEL: Record<VersionKind, string> = {
  executed: "Виконані", passed: "Успішні", failed: "Провальні", missing: "Не виконані",
};

/** Does a test fall into a block: ``statuses`` - test id → its latest status on the version of the group. */
export function matchesVersionKind(test: Row, statuses: Record<string, string>, kind: VersionKind): boolean {
  const status = statuses[test.id];
  if (kind === "missing") return !!test.active && status === undefined;
  if (kind === "executed") return status !== undefined;
  if (kind === "passed") return status === "passed";
  return status === "failed" || status === "broken";
}

/** How versions are grouped in the header (env_ai_dev#29): 4 - exact, 3 - fixes together, 2 - the minor line together. */
export type VersionLevel = 2 | 3 | 4;
export const VERSION_LEVELS: { level: VersionLevel; name: string; hint: string }[] = [
  { level: 3, name: "Фікси разом", hint: "Усі фікси однієї версії - одна група: останній результат тесту на будь-якій з них" },
  { level: 2, name: "Мінорні разом", hint: "Уся лінія мажорної версії - одна група: мінорні версії й фікси разом" },
  { level: 4, name: "Точна", hint: "Кожна збірка окремо" },
];

/** The pattern a level makes of a version, as the switch shows it: 1.75.0.3 → 1.75.0.* (3), 1.75.* (2). */
export function levelPattern(version: string | null | undefined, level: VersionLevel): string {
  const parts = (version || "1.0.0.0").split(".");
  return level >= 4 || level >= parts.length ? parts.join(".") : `${parts.slice(0, level).join(".")}.*`;
}

/**
 * The four parts of a version as the header draws them, one equal slot each: 1.75.0.* → 1, 75, 0, *;
 * 1.75.* → 1, 75, *, * (env_ai_dev#29).
 */
export function versionSlots(version: string | null | undefined): string[] {
  const parts = String(version || "").split(".").filter(Boolean).slice(0, 4);
  while (parts.length < 4) parts.push("*");
  return parts;
}

/** A click on a part of the version: a number turns into * (builds from it together), * shows the number again. */
export function levelAfterClick(slot: number, part: string): VersionLevel {
  const level = part === "*" ? slot + 1 : slot;
  return Math.min(4, Math.max(2, level)) as VersionLevel;
}
