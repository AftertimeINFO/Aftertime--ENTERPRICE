import { describe, expect, it } from "vitest";

import { activate, arrange, clampWin, frameOf, moveBy, resizeBy, stepZoom, update, withWindows, zoomLabel } from "./workspace";

describe("the window workspace", () => {
  it("arranges windows in a column, a grid or around the front one, all inside the area", () => {
    const column = arrange(["vm", "web", "emu"], "column");
    expect(column.vm).toMatchObject({ x: 0.58, y: 0, w: 0.42 });
    expect(column.emu.y + column.emu.h).toBeCloseTo(1);
    const tile = arrange(["a", "b", "c", "d"], "tile");
    expect([tile.a.x, tile.b.x, tile.c.y, tile.d.w]).toEqual([0, 0.5, 0.5, 0.5]);
    const focus = arrange(["main", "x", "y"], "focus");
    expect(focus.main.h).toBeGreaterThan(focus.x.h);
    for (const win of [...Object.values(column), ...Object.values(tile), ...Object.values(focus)]) {
      expect(win.x + win.w).toBeLessThanOrEqual(1.000001);
      expect(win.y + win.h).toBeLessThanOrEqual(1.000001);
    }
  });

  it("keeps windows a person placed when new ones appear", () => {
    const placed = moveBy(arrange(["vm"], "column"), "vm", -0.3, 0);
    const grown = withWindows(placed, ["vm", "web"]);
    expect(grown.vm.x).toBeCloseTo(0.28);
    expect(grown.web).toBeDefined();
    expect(grown.web.z).toBeGreaterThan(grown.vm.z);
    expect(withWindows(grown, ["vm", "web"])).toBe(grown);
    const untouched = withWindows(arrange(["vm"], "column"), ["vm", "web", "emu"]);
    expect([untouched.vm.h, untouched.web.y, untouched.emu.y]).toEqual([1 / 3, 1 / 3, 2 / 3]);
  });

  it("brings a window to front and restores it from the dock", () => {
    let layout = arrange(["vm", "web"], "column");
    layout = update(layout, "vm", { minimized: true });
    layout = activate(layout, "vm");
    expect(layout.vm.minimized).toBe(false);
    expect(layout.vm.z).toBeGreaterThan(layout.web.z);
    expect(activate(layout, "vm")).toBe(layout);
  });

  it("never lets a window leave the area or become too small to read", () => {
    let layout = arrange(["vm"], "column");
    layout = moveBy(layout, "vm", 5, 5);
    expect(layout.vm.x + layout.vm.w).toBeCloseTo(1);
    layout = resizeBy(layout, "vm", -2, -2);
    expect(layout.vm.w).toBeCloseTo(0.16);
    expect(clampWin({ ...layout.vm, w: 3, h: 3 })).toMatchObject({ x: 0, y: 0, w: 1, h: 1 });
  });

  it("draws a maximized window over the whole area and steps the picture scale", () => {
    const layout = update(arrange(["vm"], "column"), "vm", { maximized: true });
    expect(frameOf(layout.vm)).toEqual({ left: "0.000%", top: "0.000%", width: "100.000%", height: "100.000%" });
    expect(stepZoom("fit", 1)).toBe(1.5);
    expect(stepZoom(2, 1)).toBe(2);
    expect(stepZoom(0.5, -1)).toBe(0.5);
    expect([zoomLabel("fit"), zoomLabel(0.75)]).toEqual(["Вмістити", "75%"]);
  });
});
