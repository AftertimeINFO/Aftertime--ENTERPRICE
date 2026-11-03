import { describe, expect, it } from "vitest";

import { launchTitle, needsSummary, progressView } from "./launch";

describe("launching from Aftertime", () => {
  it("summarises what a suite needs from the stand", () => {
    expect(needsSummary({ terminal: "virtual", prro: "virtual", scanner: "manual", depositMode: "after" }))
      .toBe("термінал: virtual · ПРРО: virtual · застава after");
    expect(needsSummary(undefined)).toBe("");
  });

  it("names a run by its process or by what was chosen", () => {
    expect(launchTitle({ kind: "binding", binding: "rental-issue" })).toBe("Процес · rental-issue");
    expect(launchTitle({ kind: "center", target: { title: "група all" } })).toBe("група all");
    expect(launchTitle({ kind: "center", target: { cases: ["a", "b"], suites: [] } })).toBe("a, b");
  });

  it("turns the control center progress into a bar and a sentence", () => {
    const view = progressView({ pct: 140, suitesDone: 1, suitesTotal: 3, casesDone: 4, casesTotal: 9, elapsed: 600, remaining: 90, over: true });
    expect(view?.pct).toBe(100);
    expect(view?.text).toBe("1/3 наборів · 4/9 кейсів · минуло 10 хв 0 с · залишилось ≈1 хв 30 с · довше, ніж зазвичай");
    expect(progressView(null)).toBeNull();
  });
});
