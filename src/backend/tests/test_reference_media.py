from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services import reference_media as module


def _mock_ffprobe_result(duration_seconds: float, *, width: int = 1280, height: int = 720) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["ffprobe"],
        returncode=0,
        stdout=json.dumps(
            {
                "format": {"duration": str(duration_seconds)},
                "streams": [
                    {
                        "codec_type": "video",
                        "width": width,
                        "height": height,
                    }
                ],
            }
        ),
        stderr="",
    )


def test_upload_reference_video_saves_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(module, "_run_command", lambda _command, *, timeout: _mock_ffprobe_result(4.2))

    metadata = module.upload_reference_video("session-1", b"video-bytes", "lyrics.mp4")

    saved_path = tmp_path / metadata["shared_relative_path"]
    assert saved_path.exists()
    assert saved_path.read_bytes() == b"video-bytes"
    assert metadata["filename"] == "lyrics.mp4"
    assert metadata["reference_video_path"] == str(saved_path)
    assert metadata["duration_seconds"] == 4.2
    assert metadata["width"] == 1280
    assert metadata["height"] == 720

    listed = module.list_reference_videos("session-1")
    assert [entry["filename"] for entry in listed] == ["lyrics.mp4"]


def test_upload_reference_video_rejects_duration_outside_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(module, "_run_command", lambda _command, *, timeout: _mock_ffprobe_result(61.0))

    with pytest.raises(ValueError, match="between 1 and 60 seconds"):
        module.upload_reference_video("session-1", b"video-bytes", "too-long.mp4")

    assert not list((tmp_path / "session-1" / "_reference-videos").glob("*"))


def test_generate_storyboard_uses_uploaded_reference_video(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module, "UPLOAD_DIR", tmp_path)

    reference_dir = tmp_path / "session-1" / "_reference-videos"
    reference_dir.mkdir(parents=True, exist_ok=True)
    reference_video_path = reference_dir / "lyrics.mp4"
    reference_video_path.write_bytes(b"video-bytes")
    reference_video_metadata = {
        "id": "video-1",
        "session_id": "session-1",
        "filename": "lyrics.mp4",
        "file_path": str(reference_video_path),
        "reference_video_path": str(reference_video_path),
        "shared_relative_path": "session-1/_reference-videos/lyrics.mp4",
        "mime_type": "video/mp4",
        "size_bytes": 11,
        "duration_seconds": 4.2,
        "width": 1280,
        "height": 720,
        "created_at": "2026-04-21T12:00:00+00:00",
    }
    module._write_metadata(reference_video_path, reference_video_metadata)

    def fake_run_command(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        if command[0] == "ffmpeg":
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"storyboard")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(module, "_run_command", fake_run_command)

    metadata = module.generate_storyboard(
        "session-1",
        interval_seconds=0.5,
        clip_duration_seconds=3.0,
        columns=3,
        width=240,
    )

    storyboard_path = Path(metadata["storyboard_image_path"])
    assert storyboard_path.exists()
    assert storyboard_path.read_bytes() == b"storyboard"
    assert metadata["source_video_filename"] == "lyrics.mp4"
    assert metadata["interval_seconds"] == 0.5
    assert metadata["columns"] == 3
    assert metadata["tile_width"] == 240
    assert metadata["estimated_frames"] == 6
    assert metadata["rows"] == 2
    assert "fps=1/0.5" in metadata["ffmpeg_filter"]

    listed = module.list_storyboards("session-1")
    assert [entry["filename"] for entry in listed] == [metadata["filename"]]


def test_generate_storyboard_supports_local_crop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module, "UPLOAD_DIR", tmp_path)

    reference_dir = tmp_path / "session-1" / "_reference-videos"
    reference_dir.mkdir(parents=True, exist_ok=True)
    reference_video_path = reference_dir / "lyrics.mp4"
    reference_video_path.write_bytes(b"video-bytes")
    reference_video_metadata = {
        "id": "video-1",
        "session_id": "session-1",
        "filename": "lyrics.mp4",
        "file_path": str(reference_video_path),
        "reference_video_path": str(reference_video_path),
        "shared_relative_path": "session-1/_reference-videos/lyrics.mp4",
        "mime_type": "video/mp4",
        "size_bytes": 11,
        "duration_seconds": 4.2,
        "width": 1280,
        "height": 720,
        "created_at": "2026-04-21T12:00:00+00:00",
    }
    module._write_metadata(reference_video_path, reference_video_metadata)

    def fake_run_command(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        if command[0] == "ffmpeg":
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"storyboard")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(module, "_run_command", fake_run_command)

    metadata = module.generate_storyboard(
        "session-1",
        interval_seconds=0.5,
        clip_duration_seconds=3.0,
        columns=3,
        width=240,
        crop="10%,20%,50%,25%",
    )

    assert metadata["crop"] == {"x": 128, "y": 144, "width": 640, "height": 180}
    assert metadata["source_video_width"] == 1280
    assert metadata["source_video_height"] == 720
    assert metadata["ffmpeg_filter"].startswith("crop=640:180:128:144,")


def test_generate_storyboard_accepts_session_export_video_absolute_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    upload_root = tmp_path / "uploads"
    export_root = tmp_path / "exports"
    monkeypatch.setattr(module, "UPLOAD_DIR", upload_root)
    monkeypatch.setattr(module, "EXPORT_DIR", export_root)

    export_video_path = export_root / "session-1" / "gold.mp4"
    export_video_path.parent.mkdir(parents=True, exist_ok=True)
    export_video_path.write_bytes(b"render-bytes")

    def fake_run_command(command: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        if command[0] == "ffprobe":
            return _mock_ffprobe_result(4.2)
        if command[0] == "ffmpeg":
            output_path = Path(command[-1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"storyboard")
            return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(module, "_run_command", fake_run_command)

    metadata = module.generate_storyboard(
        "session-1",
        reference_video_path=str(export_video_path),
        output_name="gold-closeup.jpg",
        clip_duration_seconds=3.0,
        interval_seconds=0.5,
        columns=3,
        width=240,
    )

    assert metadata["source_video_filename"] == "gold.mp4"
    assert metadata["source_video_path"] == str(export_video_path)
    assert metadata["source_video_relative_path"] == "session-1/gold.mp4"
    assert Path(metadata["storyboard_image_path"]).exists()


def test_generate_storyboard_rejects_crop_outside_source_frame(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(module, "UPLOAD_DIR", tmp_path)

    reference_dir = tmp_path / "session-1" / "_reference-videos"
    reference_dir.mkdir(parents=True, exist_ok=True)
    reference_video_path = reference_dir / "lyrics.mp4"
    reference_video_path.write_bytes(b"video-bytes")
    reference_video_metadata = {
        "id": "video-1",
        "session_id": "session-1",
        "filename": "lyrics.mp4",
        "file_path": str(reference_video_path),
        "reference_video_path": str(reference_video_path),
        "shared_relative_path": "session-1/_reference-videos/lyrics.mp4",
        "mime_type": "video/mp4",
        "size_bytes": 11,
        "duration_seconds": 4.2,
        "width": 1280,
        "height": 720,
        "created_at": "2026-04-21T12:00:00+00:00",
    }
    module._write_metadata(reference_video_path, reference_video_metadata)

    with pytest.raises(ValueError, match="extends beyond the source frame width"):
        module.generate_storyboard("session-1", crop="90%,10%,20%,40%")


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="ffmpeg/ffprobe not available in this environment",
)
def test_generate_storyboard_from_lyrics_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "UPLOAD_DIR", tmp_path)

    fixture_path = Path(__file__).resolve().parents[3] / "validation-data" / "templates" / "lyrics.mp4"
    metadata = module.upload_reference_video("session-lyrics", fixture_path.read_bytes(), fixture_path.name)
    storyboard = module.generate_storyboard(
        "session-lyrics",
        reference_video_path=metadata["shared_relative_path"],
        interval_seconds=0.75,
        clip_duration_seconds=6.0,
        columns=4,
        width=220,
    )

    assert Path(storyboard["storyboard_image_path"]).exists()
    assert storyboard["estimated_frames"] >= 1
    assert storyboard["clip_duration_seconds"] > 0