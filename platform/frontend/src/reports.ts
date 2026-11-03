import type { Row } from "./api";

export const STATUSES = ["passed", "failed", "broken", "skipped", "unknown"] as const;
export const STATUS_LABELS: Record<string, string> = {
  passed: "Пройдено",
  failed: "Збій",
  broken: "Зламано",
  skipped: "Пропущено",
  unknown: "Невідомо",
};

export function formatDuration(ms: number | undefined | null): string {
  if (!ms || ms < 0) return "0 с";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} с`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} хв ${seconds % 60} с`;
  return `${Math.floor(minutes / 60)} год ${minutes % 60} хв`;
}

export type SuiteNode = { key: string; title: string; children: SuiteNode[]; tests: Row[]; counts: Record<string, number> };

function node(key: string, title: string): SuiteNode {
  return { key, title, children: [], tests: [], counts: {} };
}

/** parentSuite → suite → subSuite → tests, with status counts on every level (Allure "Suites"). */
export function suiteTree(results: Row[]): SuiteNode {
  const root = node("", "");
  for (const result of results) {
    const path = [result.parent_suite, result.suite, result.sub_suite].filter(Boolean);
    let current = root;
    current.counts[result.status] = (current.counts[result.status] || 0) + 1;
    for (const title of path.length ? path : ["Без набору"]) {
      const key = `${current.key}/${title}`;
      let child = current.children.find((item) => item.key === key);
      if (!child) current.children.push((child = node(key, title)));
      child.counts[result.status] = (child.counts[result.status] || 0) + 1;
      current = child;
    }
    current.tests.push(result);
  }
  return root;
}

export function attachmentKind(type: string | undefined): "image" | "video" | "text" | "file" {
  if (!type) return "file";
  if (type.startsWith("image/")) return "image";
  if (type.startsWith("video/")) return "video";
  if (type.startsWith("text/") || type.includes("json") || type.includes("xml")) return "text";
  return "file";
}

export function stepCount(node: Row): number {
  return (node.steps || []).reduce((total: number, step: Row) => total + 1 + stepCount(step), 0);
}

/** Percentages for a status bar; the parts always add up to the total. */
export function statusShares(stats: Row | undefined): { status: string; count: number; share: number }[] {
  const total = stats?.total || 0;
  return STATUSES.filter((status) => stats?.[status]).map((status) => ({
    status,
    count: stats![status],
    share: total ? stats![status] / total : 0,
  }));
}

export function tagsOf(result: Row): string[] {
  return (result.labels || []).filter((item: Row) => item.name === "tag").map((item: Row) => item.value)
    .filter((value: string, index: number, all: string[]) => all.indexOf(value) === index);
}
