import type { Row } from "./api";

export const PACK_STORAGE_KEY = "aftertime.pack";

/** Installed and enabled packs the operator can switch between. */
export function selectablePacks(packs: Row[]): Row[] {
  return packs.filter((pack) => pack.installed && pack.enabled && (pack.projects?.length || pack.workflows?.length));
}

/** Rows of the chosen pack; without a pack everything is visible. */
export function inPack<T extends Row>(rows: T[], pack: Row | undefined, key: "projects" | "workflows", idOf: (row: T) => string): T[] {
  if (!pack) return rows;
  const ids = new Set<string>(pack[key] || []);
  return rows.filter((row) => ids.has(idOf(row)));
}

/** A pack without a system model has only processes: the graph, events and participants are not there. */
export function hasSystemModel(pack: Row | undefined): boolean {
  return !pack || !!pack.projects?.length;
}

export function readStoredPack(): string {
  try { return window.localStorage.getItem(PACK_STORAGE_KEY) || ""; } catch { return ""; }
}

export function storePack(id: string): void {
  try { window.localStorage.setItem(PACK_STORAGE_KEY, id); } catch { /* private mode: the choice is just not remembered */ }
}
