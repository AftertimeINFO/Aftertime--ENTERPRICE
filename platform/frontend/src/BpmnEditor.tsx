import { useEffect, useRef, useState } from "react";
import Modeler from "bpmn-js/lib/Modeler";
import NavigatedViewer from "bpmn-js/lib/NavigatedViewer";
import "bpmn-js/dist/assets/diagram-js.css";
import "bpmn-js/dist/assets/bpmn-js.css";
import "bpmn-js/dist/assets/bpmn-font/css/bpmn.css";
import { api, type Row } from "./api";
import { needsSvgRepair, svgStatusLabel } from "./bpmnSvg";
import { acceptsTestPhase, phaseOptions } from "./testBindings";
import { E2ePanel, type ReplayRequest, type ScreenView } from "./E2ePanel";
import { VmScreen } from "./VmScreen";

type Props = {
  workflow: Row;
  immutable: boolean;
  onSaved: (workflow: Row) => void;
  onValidation: (validation: Row) => void;
  onError: (message: string) => void;
  onOpenReport?: (launchId: string) => void;
  /** Replay this finished run as soon as the process opens. */
  replayRequest?: ReplayRequest | null;
};

const EVENT_OPTIONS: Record<string, { value: string; label: string; definition?: string }[]> = {
  "bpmn:StartEvent": [
    { value: "none", label: "Без тригера" },
    { value: "message", label: "Повідомлення", definition: "bpmn:MessageEventDefinition" },
    { value: "timer", label: "Таймер", definition: "bpmn:TimerEventDefinition" },
    { value: "conditional", label: "Умова", definition: "bpmn:ConditionalEventDefinition" },
    { value: "signal", label: "Сигнал", definition: "bpmn:SignalEventDefinition" },
    { value: "multiple", label: "Кілька тригерів" },
    { value: "parallel_multiple", label: "Паралельні тригери" },
  ],
  "bpmn:EndEvent": [
    { value: "none", label: "Звичайне завершення" },
    { value: "message", label: "Надіслати повідомлення", definition: "bpmn:MessageEventDefinition" },
    { value: "signal", label: "Надіслати сигнал", definition: "bpmn:SignalEventDefinition" },
    { value: "error", label: "Помилка", definition: "bpmn:ErrorEventDefinition" },
    { value: "escalation", label: "Ескалація", definition: "bpmn:EscalationEventDefinition" },
    { value: "terminate", label: "Завершити весь процес", definition: "bpmn:TerminateEventDefinition" },
  ],
  "bpmn:IntermediateCatchEvent": [
    { value: "message", label: "Очікувати повідомлення", definition: "bpmn:MessageEventDefinition" },
    { value: "timer", label: "Очікувати таймер", definition: "bpmn:TimerEventDefinition" },
    { value: "conditional", label: "Очікувати умову", definition: "bpmn:ConditionalEventDefinition" },
    { value: "signal", label: "Очікувати сигнал", definition: "bpmn:SignalEventDefinition" },
  ],
  "bpmn:IntermediateThrowEvent": [
    { value: "message", label: "Надіслати повідомлення", definition: "bpmn:MessageEventDefinition" },
    { value: "signal", label: "Надіслати сигнал", definition: "bpmn:SignalEventDefinition" },
    { value: "escalation", label: "Ескалація", definition: "bpmn:EscalationEventDefinition" },
  ],
  "bpmn:BoundaryEvent": [
    { value: "message", label: "Повідомлення", definition: "bpmn:MessageEventDefinition" },
    { value: "timer", label: "Таймер", definition: "bpmn:TimerEventDefinition" },
    { value: "signal", label: "Сигнал", definition: "bpmn:SignalEventDefinition" },
    { value: "error", label: "Помилка", definition: "bpmn:ErrorEventDefinition" },
  ],
};

function eventKind(element: any): string {
  if (element?.businessObject?.parallelMultiple) return "parallel_multiple";
  if ((element?.businessObject?.eventDefinitions?.length || 0) > 1) return "multiple";
  const definition = element?.businessObject?.eventDefinitions?.[0]?.$type || "";
  return ({
    "bpmn:MessageEventDefinition": "message",
    "bpmn:TimerEventDefinition": "timer",
    "bpmn:ConditionalEventDefinition": "conditional",
    "bpmn:SignalEventDefinition": "signal",
    "bpmn:ErrorEventDefinition": "error",
    "bpmn:EscalationEventDefinition": "escalation",
    "bpmn:TerminateEventDefinition": "terminate",
  } as Record<string, string>)[definition] || "none";
}

export function BpmnEditor({ workflow, immutable, onSaved, onValidation, onError, onOpenReport, replayRequest }: Props) {
  const container = useRef<HTMLDivElement | null>(null);
  const editor = useRef<any>(null);
  const [selected, setSelected] = useState<any>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [presentation, setPresentation] = useState<Row | undefined>(workflow?.presentation);
  useEffect(() => setPresentation(workflow?.presentation), [workflow?.presentation]);
  const [bindings, setBindings] = useState<Row[]>([]);
  const [, setProcessRevision] = useState(0);
  const [ready, setReady] = useState(false);
  const [screen, setScreen] = useState<ScreenView | null>(null);
  const [screenOpen, setScreenOpen] = useState(false);
  // A running test opens the screen by itself; for a finished run a phase click or the button does.
  const replaying = screen?.at != null;
  useEffect(() => { if (screen?.active || screen?.focusPhase || replaying) setScreenOpen(true); }, [screen?.active, screen?.focusPhase, replaying]);
  const showScreen = !!screen && screenOpen;
  useEffect(() => {
    // The diagram shares the field with the machine screen: refit it whenever the split changes.
    if (!editor.current || !ready) return;
    const canvas = editor.current.get("canvas");
    window.setTimeout(() => { canvas.resized(); canvas.zoom("fit-viewport"); }, 0);
  }, [showScreen, ready]);
  useEffect(() => { api.testBindings().then(setBindings).catch(() => setBindings([])); }, []);

  useEffect(() => {
    if (!container.current || !workflow?.version?.bpmn_xml) return;
    const instance: any = immutable
      ? new NavigatedViewer({ container: container.current })
      : new Modeler({ container: container.current, keyboard: { bindTo: window } });
    editor.current = instance;
    let alive = true;
    instance.importXML(workflow.version.bpmn_xml).then(async ({ warnings }: any) => {
      if (!alive) return;
      instance.get("canvas").zoom("fit-viewport");
      setReady(true);
      if (warnings?.length) onError(warnings.map((item: any) => item.message || String(item)).join(" · "));
      if (needsSvgRepair(workflow.presentation)) {
        const { svg } = await instance.saveSVG();
        if (!alive) return;
        setPresentation(await api.saveWorkflowSvg(workflow.version.id, svg, workflow.presentation.current_bpmn_sha256));
      }
    }).catch((error: unknown) => onError(`Не вдалося відкрити BPMN XML: ${String(error)}`));
    const eventBus = instance.get("eventBus");
    const select = (event: any) => setSelected(event.newSelection?.[0] || null);
    eventBus.on("selection.changed", select);
    if (!immutable) eventBus.on("commandStack.changed", () => setDirty(true));
    return () => {
      alive = false;
      setReady(false);
      instance.destroy();
      editor.current = null;
      setSelected(null);
      setDirty(false);
    };
  }, [workflow?.version?.id, workflow?.version?.bpmn_xml, immutable, onError]);

  const updateName = (name: string) => {
    if (!editor.current || !selected || immutable) return;
    editor.current.get("modeling").updateProperties(selected, { name });
    setSelected({ ...selected });
  };
  const updateExtension = (key: string, value: string) => {
    if (!editor.current || !selected || immutable) return;
    selected.businessObject.$attrs ||= {};
    selected.businessObject.$attrs[`aftertime:${key}`] = value;
    editor.current.get("eventBus").fire("elements.changed", { elements: [selected] });
    setDirty(true);
    setSelected({ ...selected });
  };
  const processObject = (): any => {
    const root = editor.current?.get("canvas").getRootElement()?.businessObject;
    return root?.$type === "bpmn:Collaboration" ? root.participants?.[0]?.processRef : root;
  };
  const updateProcessExtension = (key: string, value: string) => {
    const process = processObject();
    if (!process || immutable) return;
    process.$attrs ||= {};
    if (value) process.$attrs[`aftertime:${key}`] = value;
    else delete process.$attrs[`aftertime:${key}`];
    setDirty(true);
    setProcessRevision((revision) => revision + 1);
  };
  const changeEventKind = (kind: string) => {
    if (!editor.current || !selected || immutable) return;
    const option = EVENT_OPTIONS[selected.type]?.find((item) => item.value === kind);
    try {
      if (kind === "multiple" || kind === "parallel_multiple") {
        const moddle = editor.current.get("moddle");
        editor.current.get("modeling").updateProperties(selected, {
          eventDefinitions: [
            moddle.create("bpmn:MessageEventDefinition"),
            moddle.create("bpmn:SignalEventDefinition"),
          ],
          parallelMultiple: kind === "parallel_multiple",
        });
        setSelected({ ...selected });
        return;
      }
      const replacement = editor.current.get("bpmnReplace").replaceElement(selected, {
        type: selected.type,
        ...(option?.definition ? { eventDefinitionType: option.definition } : {}),
      });
      setSelected(replacement);
    } catch (error) {
      onError(`Тип події не можна застосувати в цьому місці: ${String(error)}`);
    }
  };
  const save = async () => {
    if (!editor.current || immutable) return;
    setSaving(true);
    try {
      const { xml } = await editor.current.saveXML({ format: true });
      const { svg } = await editor.current.saveSVG();
      const saved = await api.saveWorkflowBpmn(workflow.version.id, xml, svg);
      setDirty(false);
      setPresentation(saved.presentation);
      onValidation(saved.validation || saved.version?.validation || {});
      onSaved(saved);
    } catch (error) {
      onError(String(error));
    } finally {
      setSaving(false);
    }
  };

  const type = selected?.type || "";
  const eventOptions = EVENT_OPTIONS[type] || [];
  const attrs = selected?.businessObject?.$attrs || {};
  return <div className={`native-bpmn-editor ${immutable ? "readonly" : "editable"} ${showScreen ? "with-screen" : ""}`}>
    <div className="native-bpmn-canvas" ref={container} />
    {showScreen && screen && <section className="bpmn-screen-pane">
      <VmScreen runId={screen.runId} active={screen.active} focusPhase={screen.focusPhase} at={screen.at} />
      <button className="link" onClick={() => setScreenOpen(false)}>Сховати екран VM</button>
    </section>}
    {screen && !screenOpen && <button className="bpmn-screen-show" onClick={() => setScreenOpen(true)}>Показати екран VM</button>}
    {!immutable && <div className="bpmn-save-strip">
      <span>{dirty ? "Є незбережені зміни" : "BPMN XML синхронізовано"}</span>
      <span className={`svg-status ${presentation?.status || "missing"}`}>{svgStatusLabel(presentation)}</span>
      {presentation?.status && presentation.status !== "missing" && <a href={api.workflowSvgUrl(workflow.version.id)} target="_blank" rel="noreferrer">SVG</a>}
      <button disabled={!dirty || saving} onClick={save}>{saving ? "Збереження…" : "Зберегти BPMN"}</button>
    </div>}
    {selected && <aside className="bpmn-properties">
      <small>ВЛАСТИВОСТІ BPMN</small>
      <b>{type.replace("bpmn:", "")}</b>
      <label>Назва<input readOnly={immutable} value={selected.businessObject?.name || ""} onChange={(event) => updateName(event.target.value)} /></label>
      {!!eventOptions.length && <label>Тип події<select disabled={immutable} value={eventKind(selected)} onChange={(event) => changeEventKind(event.target.value)}>{eventOptions.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select></label>}
      {!!eventOptions.length && eventKind(selected) !== "none" && <label>Тригер / результат<input readOnly={immutable} value={attrs["aftertime:eventRef"] || ""} placeholder="Наприклад: repair.completed" onChange={(event) => updateExtension("eventRef", event.target.value)} /></label>}
      {/Task$|Activity$|SubProcess$/.test(type) && <><label>Об’єкт<input readOnly={immutable} value={attrs["aftertime:targetRef"] || ""} onChange={(event) => updateExtension("targetRef", event.target.value)} /></label><label>Дія / capability<input readOnly={immutable} value={attrs["aftertime:capability"] || ""} onChange={(event) => updateExtension("capability", event.target.value)} /></label></>}
      {acceptsTestPhase(type) && <label>Фаза тесту<input readOnly={immutable} value={attrs["aftertime:testPhase"] || ""} placeholder="Наприклад: issue" list="bpmn-test-phases" onChange={(event) => updateExtension("testPhase", event.target.value)} /></label>}
      {type === "bpmn:BoundaryEvent" && <label className="check"><input type="checkbox" disabled={immutable} checked={selected.businessObject?.cancelActivity !== false} onChange={(event) => editor.current.get("modeling").updateProperties(selected, { cancelActivity: event.target.checked })} /> Переривати активну роботу</label>}
      <span>Використовуйте контекстне меню елемента для зміни типу задачі, gateway або події.</span>
    </aside>}
    {!selected && ready && <aside className="bpmn-properties process-overview">
      <small>ПРОЦЕС</small>
      <b>{processObject()?.name || workflow.version.name}</b>
      <label>Прив'язка тестів<select disabled={immutable} value={processObject()?.$attrs?.["aftertime:testBinding"] || ""} onChange={(event) => updateProcessExtension("testBinding", event.target.value)}>
        <option value="">Без тестів</option>
        {bindings.map((binding) => <option value={binding.id} key={binding.id}>{binding.title}</option>)}
      </select></label>
      {workflow.test && <ul className="test-coverage">
        {workflow.test.phases.map((item: Row) => <li key={item.phase} className={item.element ? "covered" : "missing"}>{item.element ? "✓" : "✗"} {item.phase}{item.element ? ` → ${item.element}` : ""}</li>)}
        {[...workflow.test.errors, ...workflow.test.warnings].map((message: string) => <li key={message} className="note">{message}</li>)}
      </ul>}
      {workflow.test && dirty && <span>Покриття оновиться після збереження.</span>}
    </aside>}
    {immutable && ready && workflow.test && <E2ePanel workflow={workflow} viewer={editor.current} onError={onError} onOpenReport={onOpenReport} onScreen={setScreen} replayRequest={replayRequest} />}
    <datalist id="bpmn-test-phases">{phaseOptions(bindings, processObject()?.$attrs?.["aftertime:testBinding"]).map((phase) => <option value={phase} key={phase} />)}</datalist>
  </div>;
}

export function BpmnCollaboration({ xml }: { xml: string }) {
  const container = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!container.current || !xml) return;
    const viewer: any = new NavigatedViewer({ container: container.current });
    viewer.importXML(xml).then(() => viewer.get("canvas").zoom("fit-viewport")).catch(() => {});
    return () => viewer.destroy();
  }, [xml]);
  return <div className="bpmn-collaboration"><header><small>BPMN 2.0 · COLLABORATION</small><b>Взаємодія організацій</b><span>Message Flow з’єднує окремі pools; відкрийте процес із таблиці для редагування.</span></header><div ref={container} /></div>;
}
