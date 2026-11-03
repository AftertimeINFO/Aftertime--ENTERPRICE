import { useEffect, useState } from "react";
import { api, type Row } from "./api";
import { firstFrameOfPhase } from "./e2eRun";
import { frameAt } from "./replay";

/** ``at`` (ms) puts the screen under a replay: the frame on screen at that moment is shown. */
type Props = { runId: string; active: boolean; focusPhase?: string | null; at?: number | null; autoplay?: boolean };

/** What the machine under test showed during a run: live while it runs, a filmstrip afterwards. */
export function VmScreen({ runId, active, focusPhase, at, autoplay }: Props) {
  const [frames, setFrames] = useState<Row[]>([]);
  const [index, setIndex] = useState<number | null>(null);
  const [large, setLarge] = useState(false);
  const [playing, setPlaying] = useState(false);
  useEffect(() => { if (autoplay) { setIndex(0); setPlaying(true); } }, [autoplay, runId]);
  useEffect(() => {
    // Frames alone (a run without a BPMN process): one frame a second.
    if (!playing) return;
    const timer = window.setInterval(() => setIndex((current) => {
      const nextIndex = (current ?? -1) + 1;
      if (nextIndex >= frames.length - 1) { setPlaying(false); return frames.length - 1; }
      return nextIndex;
    }), 1000);
    return () => window.clearInterval(timer);
  }, [playing, frames.length]);

  useEffect(() => {
    let alive = true;
    let timer = 0;
    setFrames([]);
    setIndex(null);
    const load = async () => {
      try {
        const list = await api.e2eFrames(runId);
        if (alive) setFrames(list);
      } catch { /* no frames yet */ }
      if (alive && active) timer = window.setTimeout(load, 3000);
    };
    load();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [runId, active]);

  useEffect(() => {
    if (!focusPhase) return;
    const found = firstFrameOfPhase(frames, focusPhase);
    if (found >= 0) setIndex(found);
  }, [focusPhase, frames]);

  if (!frames.length) return active ? <div className="vm-screen empty">Екран VM: чекаю на перший кадр…</div> : null;
  const replaying = at != null;
  const shown = replaying ? frameAt(frames, at) : index === null || index >= frames.length ? frames.length - 1 : index;
  if (shown < 0) return <div className="vm-screen empty">Екран VM: у цю мить кадр ще не знято</div>;
  const frame = frames[shown];
  return <div className={`vm-screen ${large ? "large" : ""}`}>
    <header>
      <small>ЕКРАН VM{replaying ? " · ВІДТВОРЕННЯ" : active && index === null ? " · НАЖИВО" : ""}</small>
      <span>{new Date(frame.captured_at).toLocaleTimeString("uk-UA")}{frame.phase ? ` · фаза ${frame.phase}` : ""}</span>
      <button className="link" onClick={() => setLarge(!large)}>{large ? "менше" : "більше"}</button>
    </header>
    <a href={api.e2eFrameUrl(frame.id)} target="_blank" rel="noreferrer"><img src={api.e2eFrameUrl(frame.id)} alt="Екран машини, на якій іде тест" /></a>
    {frames.length > 1 && !replaying && <input type="range" min={0} max={frames.length - 1} value={shown}
      onChange={(event) => setIndex(Number(event.target.value) === frames.length - 1 && active ? null : Number(event.target.value))} />}
    {!active && !replaying && frames.length > 1 && <button className="link" onClick={() => {
      if (!playing && shown >= frames.length - 1) setIndex(0);
      setPlaying(!playing);
    }}>{playing ? "⏸ пауза" : "▶ програти кадри"}</button>}
    <span className="muted">{shown + 1} з {frames.length} кадрів{active && index !== null ? " · " : ""}{active && index !== null && <button className="link" onClick={() => setIndex(null)}>наживо</button>}</span>
  </div>;
}
