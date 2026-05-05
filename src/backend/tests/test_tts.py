from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services import tts


def test_generate_tts_audio_writes_session_metadata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tts, 'UPLOAD_DIR', tmp_path / 'uploads')
    monkeypatch.setattr(tts, 'EXPORT_DIR', tmp_path / 'exports')

    def fake_generate_windows_sapi_audio(**kwargs) -> None:
        kwargs['output_path'].write_bytes(b'RIFF audio')

    monkeypatch.setattr(tts, '_generate_windows_sapi_audio', fake_generate_windows_sapi_audio)
    monkeypatch.setattr(
        tts,
        '_probe_audio',
        lambda _path: {'duration_seconds': 1.25, 'codec_name': 'pcm_s16le', 'sample_rate': '48000', 'channels': 1},
    )

    payload = tts.generate_tts_audio(
        'session-1',
        text='这是一段测试解说',
        provider='windows_sapi',
        audio_format='wav',
        output_name='voiceover.wav',
    )

    output_path = Path(payload['file_path'])
    metadata_path = output_path.parent / f"{output_path.name}{tts.METADATA_SUFFIX}"
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))

    assert output_path.read_bytes() == b'RIFF audio'
    assert payload['provider'] == 'windows_sapi'
    assert payload['shared_relative_path'] == 'session-1/_tts/voiceover.wav'
    assert payload['duration_seconds'] == 1.25
    assert metadata['text_preview'] == '这是一段测试解说'


def test_generate_tts_audio_rejects_unknown_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tts, 'UPLOAD_DIR', tmp_path / 'uploads')

    with pytest.raises(ValueError, match='Unsupported TTS provider'):
        tts.generate_tts_audio('session-1', text='hello', provider='unknown')
