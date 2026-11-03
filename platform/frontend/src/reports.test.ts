import { describe, expect, it } from "vitest";

import { attachmentKind, formatDuration, statusShares, stepCount, suiteTree, tagsOf } from "./reports";

describe("test reports", () => {
  it("groups results into parent suite → suite → sub-suite with counts", () => {
    const tree = suiteTree([
      { id: "1", status: "passed", parent_suite: "Ланцюжки", suite: "Віртуальний термінал", sub_suite: "" },
      { id: "2", status: "failed", parent_suite: "Ланцюжки", suite: "Віртуальний термінал", sub_suite: "" },
      { id: "3", status: "passed", parent_suite: "Бронювання", suite: "Веб", sub_suite: "Клієнт" },
      { id: "4", status: "broken", parent_suite: "", suite: "", sub_suite: "" },
    ]);
    expect(tree.counts).toEqual({ passed: 2, failed: 1, broken: 1 });
    expect(tree.children.map((item) => item.title)).toEqual(["Ланцюжки", "Бронювання", "Без набору"]);
    const chains = tree.children[0];
    expect(chains.counts).toEqual({ passed: 1, failed: 1 });
    expect(chains.children[0].tests.map((item) => item.id)).toEqual(["1", "2"]);
    expect(tree.children[1].children[0].children[0].title).toBe("Клієнт");
  });

  it("formats durations the way testers read them", () => {
    expect(formatDuration(0)).toBe("0 с");
    expect(formatDuration(42_400)).toBe("42 с");
    expect(formatDuration(27 * 60_000 + 54_000)).toBe("27 хв 54 с");
    expect(formatDuration(2 * 3_600_000 + 5 * 60_000)).toBe("2 год 5 хв");
  });

  it("chooses how to show an attachment", () => {
    expect(attachmentKind("image/png")).toBe("image");
    expect(attachmentKind("video/webm")).toBe("video");
    expect(attachmentKind("application/json")).toBe("text");
    expect(attachmentKind("text/plain")).toBe("text");
    expect(attachmentKind("application/zip")).toBe("file");
  });

  it("counts nested steps, status shares and unique tags", () => {
    expect(stepCount({ steps: [{ steps: [{}, {}] }, {}] })).toBe(4);
    expect(statusShares({ total: 4, passed: 3, failed: 1 })).toEqual([
      { status: "passed", count: 3, share: 0.75 }, { status: "failed", count: 1, share: 0.25 },
    ]);
    expect(tagsOf({ labels: [{ name: "tag", value: "a" }, { name: "suite", value: "s" }, { name: "tag", value: "a" }] })).toEqual(["a"]);
  });
});
