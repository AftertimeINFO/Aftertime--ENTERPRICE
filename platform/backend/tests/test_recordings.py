import io
import subprocess
import zipfile

import pytest

from app.services.recordings import concat_list, frame_offsets, pack, video_path, archive_path


def test_frames_are_ordered_by_their_offset_and_others_ignored():
    assert frame_offsets(["000002000.jpg", "000000000.jpg", "notes.txt", "seq/", "000001000.jpg"]) == [
        ("000000000.jpg", 0), ("000001000.jpg", 1000), ("000002000.jpg", 2000),
    ]


def test_each_frame_lasts_until_the_next_one(tmp_path):
    frames = [(tmp_path / "a.jpg", 0), (tmp_path / "b.jpg", 1500), (tmp_path / "c.jpg", 4000)]
    text = concat_list(frames)
    assert "duration 1.500" in text and "duration 2.500" in text and "duration 1.000" in text
    assert text.strip().splitlines()[-1].endswith("c.jpg'")


def jpeg(colour: str) -> bytes:
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={colour}:s=320x240", "-frames:v", "1", "-f", "image2", "-c:v", "mjpeg", "-"],
        capture_output=True, check=True,
    ).stdout


def recording_zip(offsets: list[int]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for index, offset in enumerate(offsets):
            bundle.writestr(f"{offset:09d}.jpg", jpeg("red" if index % 2 else "blue"))
    return buffer.getvalue()


def test_pack_makes_a_compact_mp4_and_removes_the_frames(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    archive_path("run-1").parent.mkdir(parents=True)
    archive_path("run-1").write_bytes(recording_zip([0, 1000, 2000, 5000]))
    result = pack("run-1")
    assert result["frames"] == 4 and result["duration_ms"] == 6000
    assert video_path("run-1").read_bytes()[4:8] == b"ftyp"
    assert not archive_path("run-1").exists()
    duration = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path("run-1"))],
        capture_output=True, text=True, check=True,
    ).stdout
    assert float(duration) == pytest.approx(6.0, abs=0.5)


def test_pack_fails_cleanly_without_frames(tmp_path, monkeypatch):
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    archive_path("run-2").parent.mkdir(parents=True)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("readme.txt", "no frames")
    archive_path("run-2").write_bytes(buffer.getvalue())
    with pytest.raises(ValueError):
        pack("run-2")
    assert archive_path("run-2").exists()
