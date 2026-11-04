import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, type Row } from "./api";
import { firstFrameOfPhase } from "./e2eRun";
import { frameAt, videoTime } from "./replay";
import type { Zoom } from "./workspace";

/** ``at`` (ms) puts the screen under a replay: the frame on screen at that moment is shown. */
type Props = {
  runId: string;
  active: boolean;
  /** Which recorded window of the run: the machine under test ("screen") or an application around it. */
  stream?: string;
  title?: string;
  focusPhase?: string | null;
  at?: number | null;
  autoplay?: boolean;
  /** The packed video of this window: when ready it replaces the still frames. */
  recording?: Row | null;
  /** Inside a workspace window the window frame carries the title and the size; ``zoom`` scales the picture. */
  windowed?: boolean;
  zoom?: Zoom;
};

const MAIN = "screen";

/** What a recorded window showed during a run: live while it runs, a video or a filmstrip afterwards. */
export function VmScreen({ runId, active, stream = MAIN, title, focusPhase, at, autoplay, recording, windowed, zoom = "fit" }: Props) {
  const video = useRef<HTMLVideoElement | null>(null);
  const [natural, setNatural] = useState<number | null>(null);
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
        const list = await api.e2eFrames(runId, stream);
        if (alive) setFrames(list);
      } catch { /* no frames yet */ }
      if (alive && active) timer = window.setTimeout(load, 3000);
    };
    load();
    return () => { alive = false; window.clearTimeout(timer); };
  }, [runId, active, stream]);

  useEffect(() => {
    if (!focusPhase) return;
    const found = firstFrameOfPhase(frames, focusPhase);
    if (found >= 0) setIndex(found);
  }, [focusPhase, frames]);

  const name = (title || (stream === MAIN ? "Екран VM" : stream)).toUpperCase();
  const scaled = zoom !== "fit" && natural ? { width: `${Math.round(natural * zoom)}px`, maxWidth: "none" } : undefined;
  const media = (element: ReactNode) => windowed ? <div className={`screen-media ${zoom === "fit" ? "fit" : "scaled"}`}>{element}</div> : element;
  const header = (info: ReactNode) => windowed ? <div className="screen-status">{info}</div>
    : <header>
      <small>{name}</small>
      <span>{info}</span>
      <button className="link" onClick={() => setLarge(!large)}>{large ? "менше" : "більше"}</button>
    </header>;

  if (hasVideo) {
    return <div className={`vm-screen ${large ? "large" : ""} ${windowed ? "windowed" : ""}`}>
      {header(<>{at != null ? "Відтворення · " : "Відео · "}{Math.round((recording.duration_ms || 0) / 1000)} с · {recording.frames} кадрів · {((recording.size || 0) / 1024 / 1024).toFixed(1)} МБ</>)}
      {media(<video ref={video} src={api.e2eRecordingUrl(runId, stream)} preload="auto" muted playsInline controls={at == null}
        style={scaled} onLoadedMetadata={(event) => setNatural(event.currentTarget.videoWidth || null)} />)}
      {at != null && second === null && <span className="muted">Запис цього вікна в цю мить ще не йшов</span>}
    </div>;
  }
  const packing = !active && recording?.status === "packing";
  const emptyClass = `vm-screen empty ${windowed ? "windowed" : ""}`;
  if (!frames.length) return active ? <div className={emptyClass}>Чекаю на перший кадр…</div>
    : packing ? <div className={emptyClass}>Відео пакується у фоні…</div>
    : windowed ? <div className={emptyClass}>У цьому прогоні вікно не записувалось</div> : null;
  const replaying = at != null;
  const shown = replaying ? frameAt(frames, at) : index === null || index >= frames.length ? frames.length - 1 : index;
  if (shown < 0) return <div className={emptyClass}>У цю мить кадр ще не знято</div>;
  const frame = frames[shown];
  const live = active && index === null;
  return <div className={`vm-screen ${large ? "large" : ""} ${windowed ? "windowed" : ""}`}>
    {header(<>{replaying ? "Відтворення · " : live ? "Наживо · " : ""}{new Date(frame.captured_at).toLocaleTimeString("uk-UA")}{frame.phase ? ` · фаза ${frame.phase}` : ""}</>)}
    {media(windowed
      ? <img src={api.e2eFrameUrl(frame.id)} alt={`${title || "Екран"} у ${new Date(frame.captured_at).toLocaleTimeString("uk-UA")}`} style={scaled}
        onLoad={(event) => setNatural(event.currentTarget.naturalWidth || null)} />
      : <a href={api.e2eFrameUrl(frame.id)} target="_blank" rel="noreferrer"><img src={api.e2eFrameUrl(frame.id)} alt="Екран машини, на якій іде тест" /></a>)}
    {frames.length > 1 && !replaying && <input type="range" aria-label="Кадр" min={0} max={frames.length - 1} value={shown}
      onChange={(event) => setIndex(Number(event.target.value) === frames.length - 1 && active ? null : Number(event.target.value))} />}
    <div className="screen-foot">
      {!active && !replaying && frames.length > 1 && <button className="link" onClick={() => {
        if (!playing && shown >= frames.length - 1) setIndex(0);
        setPlaying(!playing);
      }}>{playing ? "⏸ пауза" : "▶ програти кадри"}</button>}
      {packing && <span className="muted">Відео пакується у фоні; поки що — окремі кадри</span>}
      {recording?.status === "failed" && <span className="muted">Відео не запаковано: {recording.error}</span>}
      <span className="muted">{shown + 1} з {frames.length} кадрів{active && index !== null ? " · " : ""}{active && index !== null && <button className="link" onClick={() => setIndex(null)}>наживо</button>}</span>
    </div>
  </div>;
}
