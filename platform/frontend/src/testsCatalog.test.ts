import { describe, expect, it } from "vitest";

import { filterTests, parseRoute, percent, relativeTime, routeHash, sortTests, suitesOf } from "./testsCatalog";

const tests = [
  { id: "a", title: "Видача", status: "passed", flaky: false, pass_rate: 1, suites: [{ id: "issue" }], last: { start: "2026-11-04T10:00:00Z", message: "" } },
  { id: "b", title: "Повернення", status: "failed", flaky: false, pass_rate: 0.4, suites: [{ id: "chains" }], last: { start: "2026-11-04T08:00:00Z", message: "TARIFF MISMATCH" } },
  { id: "c", title: "Застава", status: "passed", flaky: true, pass_rate: 0.6, suites: [{ id: "chains" }, { id: "issue" }], last: null },
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
