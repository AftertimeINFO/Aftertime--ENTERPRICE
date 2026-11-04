/**
 * Recorded windows over the diagram: where each one is, how large, which is in front.
 *
 * Positions are fractions of the work area, so a layout survives a resized browser window.
 * Every change goes through these functions, which keep windows inside the area and large
 * enough to read; the component only turns pointer and keyboard input into calls.
 */

export type Zoom = "fit" | number;
export type Win = {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
  z: number;
  minimized: boolean;
  maximized: boolean;
  zoom: Zoom;
  /** Moved or resized by hand: arranging around new windows leaves it where it is. */
  placed?: boolean;
};
export type Layout = Record<string, Win>;
export type Arrangement = "column" | "tile" | "focus";

export const MIN_W = 0.16;
export const MIN_H = 0.16;
export const ZOOMS = [0.5, 0.75, 1, 1.5, 2];
const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value));

export function clampWin(win: Win): Win {
  const w = clamp(win.w, MIN_W, 1);
  const h = clamp(win.h, MIN_H, 1);
  return { ...win, w, h, x: clamp(win.x, 0, 1 - w), y: clamp(win.y, 0, 1 - h) };
}

function topZ(layout: Layout): number {
  return Math.max(0, ...Object.values(layout).map((win) => win.z));
}

const blank = (id: string): Win => ({ id, x: 0, y: 0, w: 0.4, h: 0.4, z: 0, minimized: false, maximized: false, zoom: "fit" });

/**
 * Windows side by side. ``column``: a column on the right, the diagram stays readable on the left;
 * ``tile``: an even grid over the whole area; ``focus``: the first window large, the others small below it.
 */
export function arrange(ids: string[], how: Arrangement, previous: Layout = {}): Layout {
  const layout: Layout = {};
  const count = ids.length;
  ids.forEach((id, index) => {
    const base = { ...blank(id), ...previous[id], minimized: false, maximized: false, placed: false, z: index + 1 };
    let place: Partial<Win>;
    if (how === "tile") {
      const columns = Math.ceil(Math.sqrt(count));
      const rows = Math.ceil(count / columns);
      place = { x: (index % columns) / columns, y: Math.floor(index / columns) / rows, w: 1 / columns, h: 1 / rows };
    } else if (how === "focus") {
      const others = count - 1;
      place = index === 0
        ? { x: 0.42, y: 0, w: 0.58, h: others ? 0.7 : 1 }
        : { x: 0.42 + ((index - 1) * 0.58) / others, y: 0.7, w: 0.58 / others, h: 0.3 };
    } else {
      place = { x: 0.58, y: index / count, w: 0.42, h: 1 / count };
    }
    layout[id] = clampWin({ ...base, ...place });
  });
  return layout;
}

/** New windows join the arrangement; windows a person placed by hand stay where they are. */
export function withWindows(layout: Layout, ids: string[], how: Arrangement = "column"): Layout {
  const missing = ids.filter((id) => !layout[id]);
  if (!missing.length) return layout;
  const fresh = arrange(ids, how);
  const next = { ...layout };
  let z = topZ(layout);
  for (const id of ids) {
    const old = layout[id];
    if (!old) next[id] = { ...fresh[id], z: ++z };
    else if (!old.placed) next[id] = { ...old, x: fresh[id].x, y: fresh[id].y, w: fresh[id].w, h: fresh[id].h };
  }
  return next;
}

export function activate(layout: Layout, id: string): Layout {
  const win = layout[id];
  if (!win) return layout;
  if (win.z === topZ(layout) && !win.minimized) return layout;
  return { ...layout, [id]: { ...win, z: topZ(layout) + 1, minimized: false } };
}

export function update(layout: Layout, id: string, change: Partial<Win>): Layout {
  const win = layout[id];
  return win ? { ...layout, [id]: clampWin({ ...win, ...change }) } : layout;
}

export function moveBy(layout: Layout, id: string, dx: number, dy: number): Layout {
  const win = layout[id];
  return win ? update(layout, id, { x: win.x + dx, y: win.y + dy, maximized: false, placed: true }) : layout;
}

export function resizeBy(layout: Layout, id: string, dw: number, dh: number): Layout {
  const win = layout[id];
  if (!win) return layout;
  // Growing past the edge pushes the window back in rather than cutting it.
  return update(layout, id, { w: clamp(win.w + dw, MIN_W, 1), h: clamp(win.h + dh, MIN_H, 1), maximized: false, placed: true });
}

export function stepZoom(zoom: Zoom, direction: 1 | -1): Zoom {
  if (zoom === "fit") return direction > 0 ? 1.5 : 0.75;
  const index = ZOOMS.indexOf(zoom);
  const next = ZOOMS[clamp((index < 0 ? 2 : index) + direction, 0, ZOOMS.length - 1)];
  return next;
}

export function zoomLabel(zoom: Zoom): string {
  return zoom === "fit" ? "Вмістити" : `${Math.round(zoom * 100)}%`;
}

/** The window as it is drawn: maximized ones take the whole area. */
export function frameOf(win: Win): { left: string; top: string; width: string; height: string } {
  const box = win.maximized ? { x: 0, y: 0, w: 1, h: 1 } : win;
  const pct = (value: number) => `${(value * 100).toFixed(3)}%`;
  return { left: pct(box.x), top: pct(box.y), width: pct(box.w), height: pct(box.h) };
}

const STORAGE_KEY = "aftertime.workspace.v1";

export function loadLayout(): Layout {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    const data = raw ? JSON.parse(raw) : {};
    const layout: Layout = {};
    for (const [id, win] of Object.entries<Partial<Win>>(data && typeof data === "object" ? data : {})) {
      if (typeof win?.x === "number" && typeof win.y === "number" && typeof win.w === "number" && typeof win.h === "number") {
        layout[id] = clampWin({ ...blank(id), ...win, id, minimized: false });
      }
    }
    return layout;
  } catch {
    return {};
  }
}

export function saveLayout(layout: Layout): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(layout));
  } catch {
    /* private mode or storage blocked: the layout simply is not remembered */
  }
}
