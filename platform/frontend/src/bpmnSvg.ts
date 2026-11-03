import type { Row } from "./api";

export function needsSvgRepair(presentation: Row | undefined): boolean {
  return !!presentation?.current_bpmn_sha256 && (presentation.status === "missing" || presentation.status === "stale");
}

export function svgStatusLabel(presentation: Row | undefined): string {
  return ({
    synced: "SVG синхронізовано",
    stale: "SVG застарів",
    incomplete: `SVG неповний: ${(presentation?.missing || []).join(", ")}`,
  } as Record<string, string>)[presentation?.status] || "SVG відсутній";
}
