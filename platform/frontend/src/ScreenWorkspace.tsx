import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import type { Row } from "./api";
import { VmScreen } from "./VmScreen";
import {
  activate, arrange, frameOf, loadLayout, moveBy, resizeBy, saveLayout, stepZoom, update, withWindows, zoomLabel,
  type Arrangement, type Layout,
} from "./workspace";

export type ScreenStream = { id: string; title: string; recording: Row | null };

type Props = {
  runId: string;
  active: boolean;
  streams: ScreenStream[];
  focusPhase?: string | null;
  at?: number | null;
  onClose?: () => void;
  /** Which work area this is; each remembers its own layout. */
  place?: string;
  /** How windows are laid out until a person arranges them. */
  initial?: Arrangement;
};

const ARRANGEMENTS: { id: Arrangement; label: string; hint: string }[] = [
  { id: "column", label: "Стовпчиком", hint: "Вікна стовпчиком праворуч, схема ліворуч" },
  { id: "focus", label: "Головне", hint: "Активне вікно велике, решта мініатюрами під ним" },
  { id: "tile", label: "Плиткою", hint: "Усі вікна рівною сіткою на всю робочу зону" },
];
const STEP = 0.02;
const KEY_HINT = "Перетягніть заголовок, щоб перемістити; кут — щоб змінити розмір. "
  + "З клавіатури: стрілки переміщують, Shift+стрілки змінюють розмір, Enter розгортає, +/− масштабують, 0 — вмістити.";

/**
 * The recorded windows of a run (the machine under test, the web applications, the emulators) as
 * windows over the diagram: each can be brought to front, moved, resized, maximized, put aside to
 * the dock and zoomed. The layout is remembered in this browser.
 */
export function ScreenWorkspace({ runId, active, streams, focusPhase, at, onClose, place = "process", initial = "column" }: Props) {
  const area = useRef<HTMLDivElement | null>(null);
  const ids = useMemo(() => streams.map((stream) => stream.id), [streams]);
  const [layout, setLayout] = useState<Layout>(() => withWindows(loadLayout(place), ids, initial));
  useEffect(() => setLayout((current) => withWindows(current, ids, initial)), [ids, initial]);
  useEffect(() => saveLayout(layout, place), [layout, place]);
  const drag = useRef<{ id: string; mode: "move" | "resize"; x: number; y: number } | null>(null);
  const front = ids.reduce<string | null>((best, id) => {
    const win = layout[id];
    if (!win || win.minimized) return best;
    return !best || win.z > layout[best].z ? id : best;
  }, null);

  const begin = (event: PointerEvent, id: string, mode: "move" | "resize") => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);
    drag.current = { id, mode, x: event.clientX, y: event.clientY };
    setLayout((current) => activate(current, id));
  };
  const follow = (event: PointerEvent) => {
    const current = drag.current;
    const box = area.current?.getBoundingClientRect();
    if (!current || !box) return;
    const dx = (event.clientX - current.x) / box.width;
    const dy = (event.clientY - current.y) / box.height;
    current.x = event.clientX;
    current.y = event.clientY;
    setLayout((value) => current.mode === "move" ? moveBy(value, current.id, dx, dy) : resizeBy(value, current.id, dx, dy));
  };
  const end = () => { drag.current = null; };

  const onKey = (event: KeyboardEvent, id: string) => {
    const arrows: Record<string, [number, number]> = { ArrowLeft: [-STEP, 0], ArrowRight: [STEP, 0], ArrowUp: [0, -STEP], ArrowDown: [0, STEP] };
    const arrow = arrows[event.key];
    const win = layout[id];
    if (arrow) {
      event.preventDefault();
      setLayout((value) => event.shiftKey ? resizeBy(value, id, arrow[0], arrow[1]) : moveBy(value, id, arrow[0], arrow[1]));
    } else if (event.key === "Enter") {
      event.preventDefault();
      setLayout((value) => update(value, id, { maximized: !win.maximized }));
    } else if (event.key === "Escape" && win.maximized) {
      setLayout((value) => update(value, id, { maximized: false }));
    } else if (event.key === "+" || event.key === "=" || event.key === "-") {
      setLayout((value) => update(value, id, { zoom: stepZoom(win.zoom, event.key === "-" ? -1 : 1) }));
    } else if (event.key === "0") {
      setLayout((value) => update(value, id, { zoom: "fit" }));
    }
  };

  const dockClick = (id: string) => setLayout((value) => {
    const win = value[id];
    if (!win) return value;
    // The front window steps aside; any other comes to front (restored if it was aside).
    return id === front ? update(value, id, { minimized: true }) : activate(value, id);
  });

  return <div className="screen-workspace" ref={area} onPointerMove={follow} onPointerUp={end} onPointerCancel={end}>
    <nav className="screen-dock" aria-label="Записані вікна прогону">
      <span className="dock-title">Вікна</span>
      {streams.map((stream) => {
        const win = layout[stream.id];
        return <button key={stream.id} className={`dock-chip ${stream.id === front ? "front" : ""} ${win?.minimized ? "aside" : ""}`}
          aria-pressed={!win?.minimized} title={win?.minimized ? "Показати вікно" : stream.id === front ? "Відкласти в док" : "Вивести на передній план"}
          onClick={() => dockClick(stream.id)}>
          {active && <i className="live-dot" aria-label="наживо" />}{stream.title}
        </button>;
      })}
      <span className="dock-sep" />
      {ARRANGEMENTS.map((item) => <button key={item.id} className="dock-layout" title={item.hint}
        onClick={() => setLayout((value) => arrange(front ? [front, ...ids.filter((id) => id !== front)] : ids, item.id, value))}>
        {item.label}
      </button>)}
      {onClose && <button className="dock-layout" title="Сховати всі вікна; схема на весь екран" onClick={onClose}>Сховати</button>}
    </nav>
    {streams.map((stream) => {
      const win = layout[stream.id];
      if (!win || win.minimized) return null;
      const isFront = stream.id === front;
      return <section key={stream.id} className={`screen-window ${isFront ? "front" : ""} ${win.maximized ? "maximized" : ""}`}
        style={{ ...frameOf(win), zIndex: 10 + win.z }} aria-label={stream.title}
        onPointerDown={() => !isFront && setLayout((value) => activate(value, stream.id))}>
        <header className="screen-titlebar" tabIndex={0} aria-describedby="screen-workspace-hint"
          onPointerDown={(event) => begin(event, stream.id, "move")}
          onDoubleClick={() => setLayout((value) => update(value, stream.id, { maximized: !win.maximized }))}
          onKeyDown={(event) => onKey(event, stream.id)} onFocus={() => !isFront && setLayout((value) => activate(value, stream.id))}>
          {active && <i className="live-dot" aria-label="наживо" />}
          <b title={stream.title}>{stream.title}</b>
          <span className="window-tools" onPointerDown={(event) => event.stopPropagation()}>
            <button title="Зменшити масштаб (−)" aria-label="Зменшити масштаб" onClick={() => setLayout((value) => update(value, stream.id, { zoom: stepZoom(win.zoom, -1) }))}>−</button>
            <button className="zoom-label" title="Вмістити у вікно (0)" onClick={() => setLayout((value) => update(value, stream.id, { zoom: "fit" }))}>{zoomLabel(win.zoom)}</button>
            <button title="Збільшити масштаб (+)" aria-label="Збільшити масштаб" onClick={() => setLayout((value) => update(value, stream.id, { zoom: stepZoom(win.zoom, 1) }))}>+</button>
            <button title={win.maximized ? "Відновити розмір (Esc)" : "Розгорнути на робочу зону (Enter)"} aria-label={win.maximized ? "Відновити розмір" : "Розгорнути"}
              onClick={() => setLayout((value) => update(value, stream.id, { maximized: !win.maximized }))}>{win.maximized ? "❐" : "□"}</button>
            <button title="Відкласти в док" aria-label="Відкласти в док" onClick={() => setLayout((value) => update(value, stream.id, { minimized: true }))}>–</button>
          </span>
        </header>
        <div className="screen-body">
          <VmScreen runId={runId} active={active} stream={stream.id} title={stream.title} recording={stream.recording}
            focusPhase={focusPhase} at={at} windowed zoom={win.zoom} />
        </div>
        {!win.maximized && <span className="screen-resize" role="presentation" title="Змінити розмір"
          onPointerDown={(event) => begin(event, stream.id, "resize")} />}
      </section>;
    })}
    <p id="screen-workspace-hint" className="sr-only">{KEY_HINT}</p>
  </div>;
}

/** Windows of a run as Aftertime reports them; runs recorded before there were several have the machine screen only. */
export function streamsOf(run: Row | null | undefined): ScreenStream[] {
  if (!run) return [];
  const listed: ScreenStream[] = (run.streams || []).map((item: Row) => ({ id: item.id, title: item.title, recording: item.recording || null }));
  if (!listed.some((item) => item.id === "screen")) {
    listed.unshift({ id: "screen", title: "Екран VM", recording: run.recording || null });
  }
  return listed;
}
