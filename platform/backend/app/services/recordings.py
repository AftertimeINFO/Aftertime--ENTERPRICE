"""Screen recordings of E2E runs: a zip of JPEG frames packed into a compact MP4.

Frames are named by milliseconds since the recording started (``000012345.jpg``). Each frame is
shown until the next one, so the video keeps real time even when frames are uneven; still screens
of a business application compress to almost nothing in H.264.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from app.services.reports import reports_dir

LAST_FRAME_SECONDS = 1.0
VIDEO_FPS = 2


def recordings_dir() -> Path:
    return reports_dir() / "recordings"


MAIN_STREAM = "screen"


def _name(run_id: str, stream: str) -> str:
    # The main window keeps the file names recordings had before there were several windows.
    return run_id if stream == MAIN_STREAM else f"{run_id}.{stream}"


def archive_path(run_id: str, stream: str = MAIN_STREAM) -> Path:
    return recordings_dir() / f"{_name(run_id, stream)}.zip"


def video_path(run_id: str, stream: str = MAIN_STREAM) -> Path:
    return recordings_dir() / f"{_name(run_id, stream)}.mp4"


def frame_offsets(names: list[str]) -> list[tuple[str, int]]:
    """``(file name, ms since start)`` of the JPEG frames, in time order; other files are ignored."""
    frames = []
    for name in names:
        stem = Path(name).stem
        if Path(name).suffix.lower() == ".jpg" and stem.isdigit():
            frames.append((name, int(stem)))
    return sorted(frames, key=lambda item: item[1])


def concat_list(frames: list[tuple[Path, int]]) -> str:
    """ffmpeg concat demuxer input: every frame lasts until the next one."""
    lines = []
    for index, (path, offset) in enumerate(frames):
        following = frames[index + 1][1] if index + 1 < len(frames) else offset + int(LAST_FRAME_SECONDS * 1000)
        lines.append(f"file '{path.as_posix()}'")
        lines.append(f"duration {max(0.001, (following - offset) / 1000):.3f}")
    if frames:
        lines.append(f"file '{frames[-1][0].as_posix()}'")  # the demuxer needs the last file twice
    return "\n".join(lines) + "\n"


def pack(run_id: str, stream: str = MAIN_STREAM) -> dict[str, Any]:
    """Turn the stored zip of frames into ``<run>[.<stream>].mp4``; the zip is removed on success."""
    archive = archive_path(run_id, stream)
    with tempfile.TemporaryDirectory() as work:
        root = Path(work)
        with zipfile.ZipFile(archive) as bundle:
            names = [name for name in bundle.namelist() if not name.endswith("/")]
            for name in names:
                target = root / Path(name).name
                with bundle.open(name) as source, target.open("wb") as copy:
                    shutil.copyfileobj(source, copy)
        frames = [(root / Path(name).name, offset) for name, offset in frame_offsets(names)]
        if not frames:
            raise ValueError("у записі немає кадрів")
        (root / "frames.txt").write_text(concat_list(frames), encoding="utf-8")
        output = root / "out.mp4"
        command = [
            "ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(root / "frames.txt"),
            # Constant 2 fps keeps the video on real time; repeated still frames cost almost nothing.
            "-vf", f"fps={VIDEO_FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
            # The demuxer gives the repeated last file a duration of its own: cut at the real end.
            "-t", f"{(frames[-1][1] + LAST_FRAME_SECONDS * 1000) / 1000:.3f}",
            "-movflags", "+faststart", str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0 or not output.exists():
            raise RuntimeError(f"ffmpeg: {result.stderr.strip()[-500:]}")
        video_path(run_id, stream).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output), video_path(run_id, stream))
    archive.unlink(missing_ok=True)
    return {
        "frames": len(frames),
        "duration_ms": frames[-1][1] + int(LAST_FRAME_SECONDS * 1000),
        "size": video_path(run_id, stream).stat().st_size,
    }
