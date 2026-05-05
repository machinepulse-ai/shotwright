"""Session-scoped text-to-speech generation for agent voiceovers."""

from __future__ import annotations

import asyncio
import html
import json
import mimetypes
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

from app.config import settings
from app.services.session_streams import publish_context_refresh

UPLOAD_DIR = Path(settings.upload_dir)
EXPORT_DIR = Path(settings.export_dir)
TTS_AUDIO_DIR = Path("_tts")
METADATA_SUFFIX = ".meta.json"
MAX_TTS_TEXT_CHARS = 20_000
SUPPORTED_AUDIO_FORMATS = {"mp3", "wav", "aac", "opus", "flac", "pcm"}


class TTSProviderError(RuntimeError):
    """Raised when a TTS provider cannot generate usable audio."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _ensure_upload_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _session_dir(session_id: str) -> Path:
    return UPLOAD_DIR / session_id


def _session_export_dir(session_id: str) -> Path:
    return EXPORT_DIR / session_id


def _asset_dir(session_id: str, asset_directory: Path) -> Path:
    return _session_dir(session_id) / asset_directory


def _ensure_asset_dir(session_id: str, asset_directory: Path) -> Path:
    directory = _asset_dir(session_id, asset_directory)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _metadata_path(asset_path: Path) -> Path:
    return asset_path.parent / f"{asset_path.name}{METADATA_SUFFIX}"


def _write_metadata(asset_path: Path, metadata: dict) -> None:
    _metadata_path(asset_path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_metadata(metadata_path: Path) -> dict | None:
    try:
        return json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _sanitize_file_name(value: str | None, fallback_stem: str, fallback_suffix: str) -> str:
    raw_name = Path(value).name if value else ""
    raw_stem = Path(raw_name).stem.strip() if raw_name else fallback_stem
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "-", raw_stem).strip().strip(".") or fallback_stem
    safe_suffix = Path(raw_name).suffix.lower() if raw_name else fallback_suffix.lower()
    if not safe_suffix.startswith("."):
        safe_suffix = f".{safe_suffix}"
    return f"{safe_stem}{safe_suffix or fallback_suffix}"


def _build_unique_path(directory: Path, file_name: str) -> Path:
    candidate = directory / file_name
    if not candidate.exists():
        return candidate

    stem = Path(file_name).stem
    suffix = Path(file_name).suffix
    for index in range(2, 1000):
        candidate = directory / f"{stem}-{index:02d}{suffix}"
        if not candidate.exists():
            return candidate
    return directory / f"{stem}-{uuid4().hex[:8]}{suffix}"


def _relative_to_session_storage(path: Path) -> str:
    resolved = path.resolve()
    for root in (UPLOAD_DIR.resolve(), EXPORT_DIR.resolve()):
        try:
            return resolved.relative_to(root).as_posix()
        except ValueError:
            continue
    return path.name


def _normalize_provider(provider: str | None, *, openai_api_key: str = "") -> str:
    requested = str(provider or "").strip().lower().replace("-", "_")
    configured = str(settings.tts_provider or "").strip().lower().replace("-", "_")
    provider_name = requested or configured or "auto"

    aliases = {
        "openai_compatible": "openai",
        "openai_compat": "openai",
        "edge_tts": "edge",
        "microsoft_edge": "edge",
        "sapi": "windows_sapi",
        "windows": "windows_sapi",
        "win_sapi": "windows_sapi",
        "eleven": "elevenlabs",
        "eleven_labs": "elevenlabs",
    }
    provider_name = aliases.get(provider_name, provider_name)

    if provider_name == "auto":
        effective_openai_key = _first_non_empty(
            openai_api_key,
            settings.tts_openai_api_key,
            settings.openai_api_key,
            os.environ.get("SHOTWRIGHT_TTS_OPENAI_API_KEY"),
            os.environ.get("SHOTWRIGHT_OPENAI_API_KEY"),
            os.environ.get("OPENAI_API_KEY"),
        )
        elevenlabs_key = _first_non_empty(
            settings.tts_elevenlabs_api_key,
            os.environ.get("SHOTWRIGHT_TTS_ELEVENLABS_API_KEY"),
            os.environ.get("ELEVENLABS_API_KEY"),
        )
        azure_key = _first_non_empty(
            settings.tts_azure_speech_key,
            os.environ.get("SHOTWRIGHT_TTS_AZURE_SPEECH_KEY"),
            os.environ.get("AZURE_SPEECH_KEY"),
            os.environ.get("SPEECH_KEY"),
        )
        azure_region = _first_non_empty(
            settings.tts_azure_region,
            os.environ.get("SHOTWRIGHT_TTS_AZURE_REGION"),
            os.environ.get("AZURE_SPEECH_REGION"),
            os.environ.get("SPEECH_REGION"),
        )
        if effective_openai_key:
            return "openai"
        if elevenlabs_key:
            return "elevenlabs"
        if azure_key and azure_region:
            return "azure"
        if os.name == "nt":
            return "windows_sapi"
        return "edge"

    if provider_name not in {"openai", "azure", "edge", "windows_sapi", "elevenlabs"}:
        raise ValueError(
            "Unsupported TTS provider. Use auto, openai/openai_compatible, azure, edge, windows_sapi, or elevenlabs."
        )
    return provider_name


def _normalize_audio_format(output_name: str | None, audio_format: str | None) -> str:
    suffix = Path(str(output_name or "")).suffix.lower().lstrip(".")
    requested = str(audio_format or suffix or "mp3").strip().lower().lstrip(".")
    aliases = {"mpeg": "mp3", "wave": "wav"}
    requested = aliases.get(requested, requested)
    if requested not in SUPPORTED_AUDIO_FORMATS:
        raise ValueError(f"Unsupported TTS audio format '{requested}'. Use mp3, wav, aac, opus, flac, or pcm.")
    return requested


def _run_command(
    command: list[str],
    *,
    timeout_seconds: int,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        env=env,
    )


def _probe_audio(file_path: Path) -> dict:
    try:
        result = _run_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ],
            timeout_seconds=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}

    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}

    streams = payload.get("streams") or []
    audio_stream = next(
        (stream for stream in streams if str(stream.get("codec_type") or "").lower() == "audio"),
        None,
    )
    format_payload = payload.get("format") or {}
    duration_text = _first_non_empty(format_payload.get("duration"), (audio_stream or {}).get("duration"))
    duration_seconds = None
    try:
        duration_seconds = round(float(duration_text), 3) if duration_text else None
    except ValueError:
        duration_seconds = None

    return {
        "duration_seconds": duration_seconds,
        "codec_name": (audio_stream or {}).get("codec_name"),
        "sample_rate": (audio_stream or {}).get("sample_rate"),
        "channels": (audio_stream or {}).get("channels"),
    }


def _convert_audio(source_path: Path, output_path: Path, audio_format: str, *, timeout_seconds: int) -> None:
    codec_args: dict[str, list[str]] = {
        "mp3": ["-codec:a", "libmp3lame", "-q:a", "3"],
        "wav": ["-codec:a", "pcm_s16le", "-ar", "48000"],
        "aac": ["-codec:a", "aac", "-b:a", "160k"],
        "opus": ["-codec:a", "libopus", "-b:a", "96k"],
        "flac": ["-codec:a", "flac"],
        "pcm": ["-f", "s16le", "-codec:a", "pcm_s16le"],
    }
    result = _run_command(
        ["ffmpeg", "-y", "-i", str(source_path), *codec_args.get(audio_format, []), str(output_path)],
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0 or not output_path.exists():
        error = (result.stderr or result.stdout or "").strip()
        raise TTSProviderError(error or "ffmpeg failed while converting generated TTS audio.")


def _write_provider_output(provider_output_path: Path, output_path: Path, audio_format: str, *, timeout_seconds: int) -> None:
    if provider_output_path.resolve() == output_path.resolve():
        return
    if provider_output_path.suffix.lower().lstrip(".") == output_path.suffix.lower().lstrip("."):
        output_path.write_bytes(provider_output_path.read_bytes())
        return
    _convert_audio(provider_output_path, output_path, audio_format, timeout_seconds=timeout_seconds)


def _generate_openai_audio(
    *,
    text: str,
    output_path: Path,
    audio_format: str,
    voice: str,
    model: str,
    instructions: str,
    speed: float | None,
    openai_api_key: str,
    base_url: str,
    timeout_seconds: int,
) -> None:
    api_key = _first_non_empty(
        openai_api_key,
        settings.tts_openai_api_key,
        settings.openai_api_key,
        os.environ.get("SHOTWRIGHT_TTS_OPENAI_API_KEY"),
        os.environ.get("SHOTWRIGHT_OPENAI_API_KEY"),
        os.environ.get("OPENAI_API_KEY"),
    )
    if not api_key:
        raise TTSProviderError("OpenAI-compatible TTS requires SHOTWRIGHT_TTS_OPENAI_API_KEY or the admin OpenAI key.")

    resolved_base_url = _first_non_empty(
        base_url,
        settings.tts_openai_base_url,
        os.environ.get("SHOTWRIGHT_TTS_OPENAI_BASE_URL"),
        "https://api.openai.com/v1",
    ).rstrip("/")
    endpoint = resolved_base_url if resolved_base_url.endswith("/audio/speech") else f"{resolved_base_url}/audio/speech"
    payload: dict[str, object] = {
        "model": _first_non_empty(model, settings.tts_openai_model, "tts-1"),
        "voice": _first_non_empty(voice, settings.tts_openai_voice, "alloy"),
        "input": text,
        "response_format": audio_format,
    }
    if instructions:
        payload["instructions"] = instructions
    if speed is not None:
        payload["speed"] = speed

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise TTSProviderError(f"OpenAI-compatible TTS request failed: {exc}") from exc

    if response.status_code >= 400:
        message = response.text[:1200] if response.text else response.reason_phrase
        raise TTSProviderError(f"OpenAI-compatible TTS returned HTTP {response.status_code}: {message}")
    output_path.write_bytes(response.content)


def _generate_azure_audio(
    *,
    text: str,
    output_path: Path,
    audio_format: str,
    voice: str,
    language: str,
    timeout_seconds: int,
) -> None:
    speech_key = _first_non_empty(
        settings.tts_azure_speech_key,
        os.environ.get("SHOTWRIGHT_TTS_AZURE_SPEECH_KEY"),
        os.environ.get("AZURE_SPEECH_KEY"),
        os.environ.get("SPEECH_KEY"),
    )
    region = _first_non_empty(
        settings.tts_azure_region,
        os.environ.get("SHOTWRIGHT_TTS_AZURE_REGION"),
        os.environ.get("AZURE_SPEECH_REGION"),
        os.environ.get("SPEECH_REGION"),
    )
    if not speech_key or not region:
        raise TTSProviderError("Azure TTS requires SHOTWRIGHT_TTS_AZURE_SPEECH_KEY and SHOTWRIGHT_TTS_AZURE_REGION.")

    resolved_voice = _first_non_empty(voice, settings.tts_azure_voice, "zh-CN-XiaoxiaoNeural")
    resolved_language = _first_non_empty(language, resolved_voice[:5], "zh-CN")
    output_format = "riff-24khz-16bit-mono-pcm" if audio_format == "wav" else "audio-24khz-48kbitrate-mono-mp3"
    provider_path = output_path if audio_format in {"mp3", "wav"} else output_path.with_suffix(".azure.mp3")
    ssml = (
        f'<speak version="1.0" xml:lang="{html.escape(resolved_language)}">'
        f'<voice name="{html.escape(resolved_voice)}">{html.escape(text)}</voice>'
        "</speak>"
    )

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
                headers={
                    "Ocp-Apim-Subscription-Key": speech_key,
                    "Content-Type": "application/ssml+xml",
                    "X-Microsoft-OutputFormat": output_format,
                    "User-Agent": "Shotwright",
                },
                content=ssml.encode("utf-8"),
            )
    except httpx.HTTPError as exc:
        raise TTSProviderError(f"Azure TTS request failed: {exc}") from exc

    if response.status_code >= 400:
        message = response.text[:1200] if response.text else response.reason_phrase
        raise TTSProviderError(f"Azure TTS returned HTTP {response.status_code}: {message}")
    provider_path.write_bytes(response.content)
    _write_provider_output(provider_path, output_path, audio_format, timeout_seconds=timeout_seconds)


def _generate_elevenlabs_audio(
    *,
    text: str,
    output_path: Path,
    audio_format: str,
    voice: str,
    model: str,
    timeout_seconds: int,
) -> None:
    api_key = _first_non_empty(
        settings.tts_elevenlabs_api_key,
        os.environ.get("SHOTWRIGHT_TTS_ELEVENLABS_API_KEY"),
        os.environ.get("ELEVENLABS_API_KEY"),
    )
    voice_id = _first_non_empty(voice, settings.tts_elevenlabs_voice_id)
    if not api_key or not voice_id:
        raise TTSProviderError("ElevenLabs TTS requires SHOTWRIGHT_TTS_ELEVENLABS_API_KEY and a voice_id.")

    base_url = _first_non_empty(settings.tts_elevenlabs_base_url, "https://api.elevenlabs.io").rstrip("/")
    provider_path = output_path if audio_format == "mp3" else output_path.with_suffix(".elevenlabs.mp3")
    payload = {
        "text": text,
        "model_id": _first_non_empty(model, settings.tts_elevenlabs_model, "eleven_multilingual_v2"),
    }

    try:
        with httpx.Client(timeout=timeout_seconds) as client:
            response = client.post(
                f"{base_url}/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": api_key, "Accept": "audio/mpeg", "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise TTSProviderError(f"ElevenLabs TTS request failed: {exc}") from exc

    if response.status_code >= 400:
        message = response.text[:1200] if response.text else response.reason_phrase
        raise TTSProviderError(f"ElevenLabs TTS returned HTTP {response.status_code}: {message}")
    provider_path.write_bytes(response.content)
    _write_provider_output(provider_path, output_path, audio_format, timeout_seconds=timeout_seconds)


def _generate_edge_audio(
    *,
    text: str,
    output_path: Path,
    audio_format: str,
    voice: str,
    rate: str,
    pitch: str,
    volume: str,
    python_executable: Path | None,
    python_env: dict[str, str] | None,
    timeout_seconds: int,
) -> None:
    if not python_executable:
        raise TTSProviderError("Edge TTS requires the Shotwright Python tool runtime.")

    provider_path = output_path if audio_format == "mp3" else output_path.with_suffix(".edge.mp3")
    script_path = output_path.with_suffix(f".edge-{uuid4().hex[:8]}.py")
    script_path.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "import asyncio",
                "import json",
                "import sys",
                "",
                "try:",
                "    import edge_tts",
                "except Exception as exc:",
                "    raise SystemExit('edge-tts is not installed in the Shotwright Python tool runtime: %s' % exc)",
                "",
                f"TEXT = {json.dumps(text, ensure_ascii=False)}",
                f"VOICE = {json.dumps(_first_non_empty(voice, settings.tts_edge_voice, 'zh-CN-XiaoxiaoNeural'))}",
                f"OUTPUT = {json.dumps(str(provider_path))}",
                f"RATE = {json.dumps(rate)}",
                f"PITCH = {json.dumps(pitch)}",
                f"VOLUME = {json.dumps(volume)}",
                "",
                "async def main():",
                "    kwargs = {'voice': VOICE}",
                "    if RATE:",
                "        kwargs['rate'] = RATE",
                "    if PITCH:",
                "        kwargs['pitch'] = PITCH",
                "    if VOLUME:",
                "        kwargs['volume'] = VOLUME",
                "    await edge_tts.Communicate(TEXT, **kwargs).save(OUTPUT)",
                "",
                "asyncio.run(main())",
            ]
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(python_env or {})
    env["PYTHONIOENCODING"] = "utf-8"
    result = _run_command([str(python_executable), str(script_path)], timeout_seconds=timeout_seconds, env=env)
    if result.returncode != 0 or not provider_path.exists():
        error = (result.stderr or result.stdout or "").strip()
        raise TTSProviderError(error or "edge-tts finished without producing audio.")
    _write_provider_output(provider_path, output_path, audio_format, timeout_seconds=timeout_seconds)


def _generate_windows_sapi_audio(
    *,
    text: str,
    output_path: Path,
    audio_format: str,
    voice: str,
    rate: str,
    timeout_seconds: int,
) -> None:
    if os.name != "nt":
        raise TTSProviderError("Windows SAPI TTS is only available on Windows hosts.")

    provider_path = output_path if audio_format == "wav" else output_path.with_suffix(".sapi.wav")
    text_path = output_path.with_suffix(f".sapi-{uuid4().hex[:8]}.txt")
    script_path = output_path.with_suffix(f".sapi-{uuid4().hex[:8]}.ps1")
    text_path.write_text(text, encoding="utf-8")
    script_path.write_text(
        "\n".join(
            [
                "param([string]$TextPath, [string]$OutputPath, [string]$VoiceName, [string]$SpeechRate)",
                "Add-Type -AssemblyName System.Speech",
                "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer",
                "try {",
                "  if (-not [string]::IsNullOrWhiteSpace($VoiceName)) {",
                "    try { $synth.SelectVoice($VoiceName) } catch {}",
                "  }",
                "  $rateValue = 0",
                "  if ([int]::TryParse($SpeechRate, [ref]$rateValue)) {",
                "    if ($rateValue -lt -10) { $rateValue = -10 }",
                "    if ($rateValue -gt 10) { $rateValue = 10 }",
                "    $synth.Rate = $rateValue",
                "  }",
                "  $text = Get-Content -LiteralPath $TextPath -Raw -Encoding UTF8",
                "  $synth.SetOutputToWaveFile($OutputPath)",
                "  $synth.Speak($text)",
                "} finally {",
                "  $synth.Dispose()",
                "}",
            ]
        ),
        encoding="utf-8",
    )
    result = _run_command(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-TextPath",
            str(text_path),
            "-OutputPath",
            str(provider_path),
            "-VoiceName",
            voice,
            "-SpeechRate",
            rate,
        ],
        timeout_seconds=timeout_seconds,
    )
    if result.returncode != 0 or not provider_path.exists():
        error = (result.stderr or result.stdout or "").strip()
        raise TTSProviderError(error or "Windows SAPI finished without producing audio.")
    _write_provider_output(provider_path, output_path, audio_format, timeout_seconds=timeout_seconds)


def _publish_context_refresh_in_background(session_id: str, reason: str, **payload: object) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(publish_context_refresh(session_id, reason, **payload))


def _load_asset_metadata(directory: Path, *, limit: int | None = None) -> list[dict]:
    if not directory.exists():
        return []

    entries: list[dict] = []
    for metadata_path in sorted(directory.glob(f"*{METADATA_SUFFIX}"), key=lambda path: path.stat().st_mtime, reverse=True):
        metadata = _read_metadata(metadata_path)
        if not metadata:
            continue
        file_path = Path(str(metadata.get("file_path") or ""))
        if not file_path.exists():
            continue
        entries.append(metadata)
        if limit is not None and len(entries) >= limit:
            break
    return entries


def list_tts_audio(session_id: str, *, limit: int | None = None) -> list[dict]:
    return _load_asset_metadata(_asset_dir(session_id, TTS_AUDIO_DIR), limit=limit)


def generate_tts_audio(
    session_id: str,
    *,
    text: str,
    provider: str | None = None,
    voice: str = "",
    model: str = "",
    language: str = "",
    audio_format: str | None = None,
    output_name: str | None = None,
    instructions: str = "",
    speed: float | None = None,
    rate: str = "",
    pitch: str = "",
    volume: str = "",
    base_url: str = "",
    openai_api_key: str = "",
    python_executable: Path | None = None,
    python_env: dict[str, str] | None = None,
    timeout_seconds: int = 180,
) -> dict:
    _ensure_upload_dir()
    normalized_text = str(text or "").strip()
    if not normalized_text:
        raise ValueError("text is required for TTS generation.")
    if len(normalized_text) > MAX_TTS_TEXT_CHARS:
        raise ValueError(f"TTS text exceeds the {MAX_TTS_TEXT_CHARS} character limit; split it into shorter segments.")

    resolved_provider = _normalize_provider(provider, openai_api_key=openai_api_key)
    resolved_format = _normalize_audio_format(output_name, audio_format)
    output_dir = _ensure_asset_dir(session_id, TTS_AUDIO_DIR)
    safe_output_name = _sanitize_file_name(output_name, f"tts-{uuid4().hex[:8]}", f".{resolved_format}")
    if Path(safe_output_name).suffix.lower().lstrip(".") != resolved_format:
        safe_output_name = f"{Path(safe_output_name).stem}.{resolved_format}"
    output_path = _build_unique_path(output_dir, safe_output_name)
    timeout = max(10, int(timeout_seconds or 180))

    if resolved_provider == "openai":
        _generate_openai_audio(
            text=normalized_text,
            output_path=output_path,
            audio_format=resolved_format,
            voice=voice,
            model=model,
            instructions=instructions,
            speed=speed,
            openai_api_key=openai_api_key,
            base_url=base_url,
            timeout_seconds=timeout,
        )
    elif resolved_provider == "azure":
        _generate_azure_audio(
            text=normalized_text,
            output_path=output_path,
            audio_format=resolved_format,
            voice=voice,
            language=language,
            timeout_seconds=timeout,
        )
    elif resolved_provider == "elevenlabs":
        _generate_elevenlabs_audio(
            text=normalized_text,
            output_path=output_path,
            audio_format=resolved_format,
            voice=voice,
            model=model,
            timeout_seconds=timeout,
        )
    elif resolved_provider == "edge":
        _generate_edge_audio(
            text=normalized_text,
            output_path=output_path,
            audio_format=resolved_format,
            voice=voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
            python_executable=python_executable,
            python_env=python_env,
            timeout_seconds=timeout,
        )
    elif resolved_provider == "windows_sapi":
        _generate_windows_sapi_audio(
            text=normalized_text,
            output_path=output_path,
            audio_format=resolved_format,
            voice=voice,
            rate=rate,
            timeout_seconds=timeout,
        )
    else:
        raise ValueError(f"Unsupported TTS provider: {resolved_provider}")

    if not output_path.exists() or output_path.stat().st_size <= 0:
        raise TTSProviderError("TTS provider finished without producing a non-empty audio file.")

    probe = _probe_audio(output_path)
    created_at = _utcnow().isoformat()
    metadata = {
        "id": uuid4().hex[:12],
        "session_id": session_id,
        "provider": resolved_provider,
        "model": _first_non_empty(model, settings.tts_openai_model if resolved_provider == "openai" else ""),
        "voice": _first_non_empty(
            voice,
            settings.tts_edge_voice if resolved_provider == "edge" else "",
            settings.tts_azure_voice if resolved_provider == "azure" else "",
            settings.tts_openai_voice if resolved_provider == "openai" else "",
        ),
        "filename": output_path.name,
        "file_path": str(output_path),
        "tts_audio_path": str(output_path),
        "shared_relative_path": _relative_to_session_storage(output_path),
        "mime_type": mimetypes.guess_type(output_path.name)[0] or f"audio/{resolved_format}",
        "format": resolved_format,
        "size_bytes": output_path.stat().st_size,
        "duration_seconds": probe.get("duration_seconds"),
        "codec_name": probe.get("codec_name"),
        "sample_rate": probe.get("sample_rate"),
        "channels": probe.get("channels"),
        "text_char_count": len(normalized_text),
        "text_preview": normalized_text[:280],
        "created_at": created_at,
    }
    _write_metadata(output_path, metadata)
    _publish_context_refresh_in_background(
        session_id,
        "tts_audio.generated",
        tts_audio_path=metadata["shared_relative_path"],
        provider=resolved_provider,
    )
    return metadata
