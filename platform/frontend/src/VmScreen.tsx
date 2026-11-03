import { useEffect, useRef, useState } from "react";
import { api, type Row } from "./api";
import { firstFrameOfPhase } from "./e2eRun";
import { frameAt, videoTime } from "./replay";

/** ``at`` (ms) puts the screen under a replay: the frame on screen at that moment is shown. */
type Props = {
  runId: string;
  active: boolean;
  focusPhase?: string | null;
  at?: number | null;
  autoplay?: boolean;
  /** The packed screen video of the run: when ready it replaces the still frames. */
  recording?: Row | null;
};

/** What the machine under test showed during a run: live while it runs, a filmstrip afterwards. */
export function VmScreen({ runId, active, focusPhase, at, autoplay, recording }: Props) {
  const video = useRef<HTMLVideoElement | null>(null);
  const hasVideo = !active && recording?.status === "ready";
  const second = hasVideo && at != null ? videoTime({ recording }, at) : null;
  useEffect(() => {
    // Under a replay the video follows the replayed moment instead of playing on its own.
    if (!video.current || second === null) return;
    video.current.pause();
    if (Math.abs(video.current.currentTime - second) > 0.2) video.current.currentTime = second;
  }, [second]);
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

  if (hasVideo) {
    return <div className={`vm-screen ${large ? "large" : ""}`}>
      <header>
        <small>ЕКРАН VM · ВІДЕО{at != null ? " · ВІДТВОРЕННЯ" : ""}</small>
        <span>{Math.round((recording.duration_ms || 0) / 1000)} с · {recording.frames} кадрів · {((recording.size || 0) / 1024 / 1024).toFixed(1)} МБ</span>
        <button className="link" onClick={() => setLarge(!large)}>{large ? "менше" : "більше"}</button>
      </header>
      <video ref={video} src={api.e2eRecordingUrl(runId)} preload="auto" muted playsInline controls={at == null} />
      {at != null && second === null && <span className="muted">Запис екрана в цю мить ще не йшов</span>}
    </div>;
  }
  const packing = !active && recording?.status === "packing";
  if (!frames.length) return active ? <div className="vm-screen empty">Екран VM: чекаю на перший кадр…</div>
    : packing ? <div className="vm-screen empty">Відео екрана пакується у фоні…</div> : null;
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
    {packing && <span className="muted">Відео екрана пакується у фоні; поки що — окремі кадри</span>}
    {recording?.status === "failed" && <span className="muted">Відео не запаковано: {recording.error}</span>}
    <span className="muted">{shown + 1} з {frames.length} кадрів{active && index !== null ? " · " : ""}{active && index !== null && <button className="link" onClick={() => setIndex(null)}>наживо</button>}</span>
  </div>;
}
