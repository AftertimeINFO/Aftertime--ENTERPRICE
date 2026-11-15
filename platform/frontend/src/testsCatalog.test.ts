import { describe, expect, it } from "vitest";

import {
  filterTests, levelAfterClick, levelPattern, matchesVersionKind, versionSlots, nextSort, parseRoute, percent, relativeTime, routeHash, sortTests, suitesOf, trendFailures, versionOptionLabel,
} from "./testsCatalog";

const tests = [
  { id: "a", title: "Видача", status: "passed", flaky: false, pass_rate: 1, suites: [{ id: "issue" }], trend: ["passed", "passed"], last: { start: "2026-11-04T10:00:00Z", duration_ms: 60_000, message: "" } },
  { id: "b", title: "Повернення", status: "failed", flaky: false, pass_rate: 0.4, suites: [{ id: "chains" }], trend: ["failed", "passed", "failed"], last: { start: "2026-11-04T08:00:00Z", duration_ms: 900_000, message: "TARIFF MISMATCH" } },
  { id: "c", title: "Застава", status: "passed", flaky: true, pass_rate: 0.6, suites: [{ id: "chains" }, { id: "issue" }], trend: ["failed", "passed", "passed", "passed"], last: null },
  { id: "d", title: "Бронювання", status: "never", flaky: false, pass_rate: null, suites: [{ id: "booking" }], last: null },
];
const none = { query: "", suite: "", statuses: [], flakyOnly: false };

describe("the test suite", () => {
  it("filters by text, including the failure message, suite, status and flakiness", () => {
    expect(filterTests(tests, { ...none, query: "mismatch" }).map((item) => item.id)).toEqual(["b"]);
    expect(filterTests(tests, { ...none, suite: "issue" }).map((item) => item.id)).toEqual(["a", "c"]);
    expect(filterTests(tests, { ...none, statuses: ["never", "failed"] }).map((item) => item.id)).toEqual(["b", "d"]);
    expect(filterTests(tests, { ...none, flakyOnly: true }).map((item) => item.id)).toEqual(["c"]);
  });

  it("puts what needs attention first, or sorts by name, recency or stability", () => {
    expect(sortTests(tests, "attention").map((item) => item.id)).toEqual(["b", "d", "c", "a"]);
    expect(sortTests(tests, "name").map((item) => item.id)).toEqual(["d", "a", "c", "b"]);
    expect(sortTests(tests, "recent").map((item) => item.id)[0]).toBe("a");
    expect(sortTests(tests, "stability").map((item) => item.id)).toEqual(["b", "c", "a", "d"]);
  });

  it("sorts by any column, both ways (env_ai_dev#24)", () => {
    expect(sortTests(tests, "duration").map((item) => item.id)).toEqual(["b", "a", "d", "c"]);
    expect(sortTests(tests, "trend").map((item) => item.id)).toEqual(["b", "c", "a", "d"]);
    expect(trendFailures(tests[3])).toBe(-1);
    expect(sortTests(tests, "name", true).map((item) => item.id)).toEqual(["b", "c", "a", "d"]);
    expect(sortTests(tests, "stability", true).map((item) => item.id)).toEqual(["d", "a", "c", "b"]);
    expect(nextSort({ sort: "name", reversed: false }, "name")).toEqual({ sort: "name", reversed: true });
    expect(nextSort({ sort: "name", reversed: true }, "recent")).toEqual({ sort: "recent", reversed: false });
  });

  it("sorts by the last successful run, never passed last (env_ai_dev#29)", () => {
    const rows = [
      { id: "x", title: "X", last_passed: { start: "2026-11-14T10:00:00Z", version: "1.75.0.2" } },
      { id: "y", title: "Y", last_passed: null },
      { id: "z", title: "Z", last_passed: { start: "2026-11-15T10:00:00Z", version: "1.75.0.3" } },
    ];
    expect(sortTests(rows, "passed").map((item) => item.id)).toEqual(["z", "x", "y"]);
    expect(sortTests(rows, "passed", true).map((item) => item.id)).toEqual(["y", "x", "z"]);
  });

  it("filters by a block of a version group and labels versions in short (env_ai_dev#29)", () => {
    const statuses = { a: "passed", b: "failed", c: "broken" };
    const rows = [{ id: "a", active: true }, { id: "b", active: true }, { id: "c", active: true }, { id: "d", active: true }, { id: "e", active: false }];
    const ids = (kind: Parameters<typeof matchesVersionKind>[2]) => rows.filter((row) => matchesVersionKind(row, statuses, kind)).map((row) => row.id);
    expect(ids("passed")).toEqual(["a"]);
    expect(ids("failed")).toEqual(["b", "c"]);
    expect(ids("executed")).toEqual(["a", "b", "c"]);
    expect(ids("missing")).toEqual(["d"]);
    expect(versionOptionLabel({ version: "1.75.0.2", passed: 12, failed: 10, executed: 22, tests: 56, percent: 39 }, { best: true }))
      .toBe("1.75.0.2 · ✓ 12 · ✗ 10 · виконано 22/56 (39%) · найкраща");
  });

  it("draws a version in four slots and toggles grouping by a click on a part (env_ai_dev#29)", () => {
    expect(versionSlots("1.75.0.*")).toEqual(["1", "75", "0", "*"]);
    expect(versionSlots("1.75.*")).toEqual(["1", "75", "*", "*"]);
    expect(versionSlots("1.75.0.3")).toEqual(["1", "75", "0", "3"]);
    expect(levelAfterClick(3, "3")).toBe(3);
    expect(levelAfterClick(2, "0")).toBe(2);
    expect(levelAfterClick(3, "*")).toBe(4);
    expect(levelAfterClick(2, "*")).toBe(3);
  });

  it("names grouping levels by the patterns they make (env_ai_dev#29)", () => {
    expect(levelPattern("1.75.0.3", 3)).toBe("1.75.0.*");
    expect(levelPattern("1.75.0.3", 2)).toBe("1.75.*");
    expect(levelPattern("1.75.0.3", 4)).toBe("1.75.0.3");
    expect(versionOptionLabel({ version: "1.75.0.*", members: ["1.75.0.3", "1.75.0.2"], passed: 17, failed: 5, executed: 22, tests: 56, percent: 39 }))
      .toBe("1.75.0.* (2 збірки) · ✓ 17 · ✗ 5 · виконано 22/56 (39%)");
  });

  it("lists suites once and reads times and rates the way people say them", () => {
    expect(suitesOf(tests).map((suite) => suite.id)).toEqual(["booking", "chains", "issue"]);
    const now = Date.parse("2026-11-04T12:00:00Z");
    expect(relativeTime("2026-11-04T11:55:00Z", now)).toBe("5 хв тому");
    expect(relativeTime("2026-11-04T08:00:00Z", now)).toBe("4 год тому");
    expect(relativeTime(null, now)).toBe("ніколи");
    expect(percent(0.33)).toBe("33%");
    expect(percent(null)).toBe("—");
  });

  it("keeps the view in the address", () => {
    expect(parseRoute("#/tests/order-issue-case")).toEqual({ section: "tests", id: "order-issue-case", tab: null });
    expect(parseRoute("#/results/matrix")).toEqual({ section: "results", id: null, tab: "matrix" });
    expect(parseRoute("")).toEqual({ section: "", id: null, tab: null });
    expect(routeHash("tests", "a b")).toBe("#/tests/a%20b");
    expect(routeHash("results", null, "summary")).toBe("#/results/summary");
  });
});
