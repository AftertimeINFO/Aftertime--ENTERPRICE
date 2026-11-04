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
export type TestSort = "attention" | "name" | "recent" | "stability";

const ATTENTION: Record<string, number> = { failed: 0, broken: 1, unknown: 3, skipped: 4, never: 5, passed: 6 };

export function filterTests(tests: Row[], filters: TestFilters): Row[] {
  const query = filters.query.trim().toLowerCase();
  return tests.filter((test) =>
    (!query || `${test.id} ${test.title} ${test.last?.message || ""}`.toLowerCase().includes(query))
    && (!filters.suite || (test.suites || []).some((suite: Row) => suite.id === filters.suite))
    && (!filters.statuses.length || filters.statuses.includes(test.status))
    && (!filters.flakyOnly || test.flaky));
}

/** "attention" puts what needs a look first: failures, then flaky tests, then the rest. */
export function sortTests(tests: Row[], sort: TestSort): Row[] {
  const byName = (left: Row, right: Row) => String(left.title).localeCompare(String(right.title), "uk");
  const started = (test: Row) => (test.last?.start ? Date.parse(test.last.start) : 0);
  const sorted = [...tests];
  if (sort === "name") return sorted.sort(byName);
  if (sort === "recent") return sorted.sort((left, right) => started(right) - started(left) || byName(left, right));
  if (sort === "stability") return sorted.sort((left, right) => (left.pass_rate ?? 2) - (right.pass_rate ?? 2) || byName(left, right));
  return sorted.sort((left, right) =>
    (ATTENTION[left.status] ?? 9) - (ATTENTION[right.status] ?? 9) || Number(right.flaky) - Number(left.flaky) || byName(left, right));
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
