import { useEffect, useState } from "react";
import { api, type Row } from "./api";
import { BpmnEditor } from "./BpmnEditor";
import type { ReplayRequest } from "./E2ePanel";

/**
 * Tabs of the application (env_ai_dev#8): the section picked in the top menu is always the first tab;
 * a test, a process or a replay of a recorded run opens a tab of its own next to it, so several can be
 * kept open and switched between without losing where each one is.
 */
export type AppTab =
  | { key: string; kind: "test"; title: string; testId: string }
  | { key: string; kind: "process"; title: string; versionId: string }
  | { key: string; kind: "replay"; title: string; versionId: string; runId: string };

export const MAIN_TAB = "main";
const STORE = "aftertime.app-tabs";

export function loadTabs(): { tabs: AppTab[]; active: string } {
  try {
    const stored = JSON.parse(window.localStorage.getItem(STORE) || "null");
    if (stored && Array.isArray(stored.tabs)) {
      const tabs = stored.tabs.filter((tab: Row) => tab && typeof tab.key === "string" && ["test", "process", "replay"].includes(tab.kind)) as AppTab[];
      return { tabs, active: tabs.some((tab) => tab.key === stored.active) ? stored.active : MAIN_TAB };
    }
  } catch { /* no storage in this browser: start with the section only */ }
  return { tabs: [], active: MAIN_TAB };
}

export function saveTabs(tabs: AppTab[], active: string) {
  try { window.localStorage.setItem(STORE, JSON.stringify({ tabs, active })); } catch { /* not kept: fine */ }
}

type BarProps = {
  mainTitle: string;
  tabs: AppTab[];
  active: string;
  onActivate: (key: string) => void;
  onClose: (key: string) => void;
};

const ICON: Record<AppTab["kind"], string> = { test: "▤", process: "◇", replay: "▶" };

export function AppTabsBar({ mainTitle, tabs, active, onActivate, onClose }: BarProps) {
  return <nav className="app-tabs" role="tablist" aria-label="Відкриті вкладки">
    <button role="tab" aria-selected={active === MAIN_TAB} className={`app-tab main ${active === MAIN_TAB ? "active" : ""}`} onClick={() => onActivate(MAIN_TAB)}>
      {mainTitle}
    </button>
    {tabs.map((tab) => <span key={tab.key} className={`app-tab kind-${tab.kind} ${active === tab.key ? "active" : ""}`}
      onAuxClick={(event) => { if (event.button === 1) onClose(tab.key); }}>
      <button role="tab" aria-selected={active === tab.key} title={tab.title} onClick={() => onActivate(tab.key)}>
        <i aria-hidden="true">{ICON[tab.kind]}</i>{tab.title}
      </button>
      <button className="app-tab-close" aria-label={`Закрити вкладку ${tab.title}`} title="Закрити вкладку" onClick={() => onClose(tab.key)}>✕</button>
    </span>)}
  </nav>;
}

type ProcessProps = {
  versionId: string;
  replay?: ReplayRequest | null;
  shown: boolean;
  onError: (message: string) => void;
  onOpenReport?: (launchId: string) => void;
  /** Opens this process in the BPMN section (to edit a draft copy and so on). */
  onOpenInSection?: (workflow: Row) => void;
};

/** A process in a tab of its own; with ``replay`` the recorded run is replayed as soon as it opens. */
export function ProcessTabPage({ versionId, replay, shown, onError, onOpenReport, onOpenInSection }: ProcessProps) {
  const [workflow, setWorkflow] = useState<Row | null>(null);
  useEffect(() => {
    setWorkflow(null);
    api.workflow(versionId).then(setWorkflow).catch((error) => onError(String(error)));
  }, [versionId, onError]);
  // The diagram cannot be laid out in a hidden tab: a tab restored in the background opens when first shown.
  const [seen, setSeen] = useState(shown);
  useEffect(() => { if (shown) setSeen(true); }, [shown]);
  if (!workflow || !seen) return <div className="process-tab-page"><p className="process-tab-loading">Завантаження процесу…</p></div>;
  return <div className="process-tab-page">
    <div className="process-tab-head">
      <b>{workflow.version?.name}</b>
      <span>v{workflow.version?.version} · {workflow.version?.status}</span>
      {onOpenInSection && <button onClick={() => onOpenInSection(workflow)}>Відкрити в розділі BPMN</button>}
    </div>
    <BpmnEditor workflow={workflow} immutable={workflow.version?.status !== "draft"} shown={shown}
      onSaved={setWorkflow} onValidation={() => {}} onError={onError} onOpenReport={onOpenReport} replayRequest={replay || null} />
  </div>;
}
