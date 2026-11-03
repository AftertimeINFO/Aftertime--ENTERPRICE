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

/**
 * The contour chosen in this browser tab, or null when nothing was chosen yet.
 * Kept per tab only: opening Aftertime again starts from the configured default contour.
 */
export function readStoredPack(): string | null {
  try { return window.sessionStorage.getItem(PACK_STORAGE_KEY); } catch { return null; }
}

export function storePack(id: string): void {
  try { window.sessionStorage.setItem(PACK_STORAGE_KEY, id); } catch { /* private mode: the choice is just not remembered */ }
}

/** The process a contour opens with: the configured one if it is in the contour, else its first. */
export function defaultProcess(pack: Row | undefined, configured: string): string | null {
  const workflows: string[] = pack?.workflows || [];
  if (!workflows.length) return null;
  return workflows.includes(configured) ? configured : workflows[0];
}
