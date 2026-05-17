from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
import contextlib
import os
import tempfile
import shutil

from imageio_ffmpeg import get_ffmpeg_exe


def format_timestamp(seconds: float) -> str:
    seconds = max(float(seconds), 0.0)
    hours, remainder = divmod(int(seconds), 3600)
    minutes, whole_seconds = divmod(remainder, 60)
    fractional = seconds - int(seconds)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{int(fractional * 10):1d}"
    return f"{minutes:02d}:{whole_seconds:02d}.{int(fractional * 10):1d}"


def compact_text(text: str, limit: int = 90) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 1].rstrip()}…"


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str

    @property
    def timestamp_label(self) -> str:
        return f"{format_timestamp(self.start)} - {format_timestamp(self.end)}"


@dataclass(frozen=True)
class TranscriptResult:
    source_name: str
    media_kind: str
    transcript_text: str
    segments: tuple[TranscriptSegment, ...]
    language: str | None = None
    source_path: str | None = None

    @property
    def has_segments(self) -> bool:
        return bool(self.segments)


def _ensure_ffmpeg_on_path() -> None:
    ffmpeg_path = Path(get_ffmpeg_exe())
    shim_dir = Path(tempfile.gettempdir()) / "social_emotions_ffmpeg"
    shim_dir.mkdir(parents=True, exist_ok=True)
    shim_path = shim_dir / "ffmpeg"
    if not shim_path.exists():
        try:
            shim_path.symlink_to(ffmpeg_path)
        except OSError:
            shutil.copy2(ffmpeg_path, shim_path)
            shim_path.chmod(0o755)

    ffmpeg_dir = str(shim_dir)
    current_path = os.environ.get("PATH", "")
    if ffmpeg_dir not in current_path.split(os.pathsep):
        os.environ["PATH"] = os.pathsep.join([ffmpeg_dir, current_path]) if current_path else ffmpeg_dir


def _suffix_for_name(filename: str | None) -> str:
    if not filename:
        return ".wav"
    suffix = Path(filename).suffix
    return suffix if suffix else ".wav"


def _materialize_source(source: str | Path | bytes | bytearray | memoryview, filename: str | None = None) -> tuple[Path, Callable[[], None]]:
    if isinstance(source, Path):
        return source, lambda: None
    if isinstance(source, str):
        return Path(source), lambda: None

    suffix = _suffix_for_name(filename)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        handle.write(bytes(source))
        handle.flush()
    finally:
        handle.close()

    temp_path = Path(handle.name)
    return temp_path, lambda: temp_path.unlink(missing_ok=True)


class AudioEmotionsProcessor:
    def __init__(self, model_name: str = "small", device: str = "cpu", language: str | None = None) -> None:
        self.model_name = model_name
        self.device = device
        self.language = language
        self._model = None

    def _load_model(self):
        if self._model is None:
            import whisper

            self._model = whisper.load_model(self.model_name, device=self.device)
        return self._model

    def transcribe(
        self,
        source: str | Path | bytes | bytearray | memoryview,
        filename: str | None = None,
        *,
        media_kind: str = "audio",
    ) -> TranscriptResult:
        source_path, cleanup = _materialize_source(source, filename)
        try:
            return self.transcribe_file(source_path, filename=filename or source_path.name, media_kind=media_kind)
        finally:
            cleanup()

    def transcribe_file(
        self,
        source_path: str | Path,
        *,
        filename: str | None = None,
        media_kind: str = "audio",
    ) -> TranscriptResult:
        _ensure_ffmpeg_on_path()
        model = self._load_model()
        source_path = Path(source_path)
        transcript = model.transcribe(
            str(source_path),
            fp16=False,
            verbose=False,
            language=self.language,
            task="transcribe",
            temperature=0.0,
            condition_on_previous_text=True,
            beam_size=5,
            best_of=5,
        )
        segments = tuple(
            TranscriptSegment(
                start=float(segment["start"]),
                end=float(segment["end"]),
                text=str(segment["text"]).strip(),
            )
            for segment in transcript.get("segments", [])
            if str(segment.get("text", "")).strip()
        )
        transcript_text = " ".join(segment.text for segment in segments).strip()
        return TranscriptResult(
            source_name=filename or source_path.name,
            media_kind=media_kind,
            transcript_text=transcript_text,
            segments=segments,
            language=transcript.get("language"),
            source_path=str(source_path),
        )


def normalize_audio_input(source: Any) -> bytes:
    if isinstance(source, bytes):
        return source
    if isinstance(source, bytearray):
        return bytes(source)
    if isinstance(source, memoryview):
        return source.tobytes()
    if isinstance(source, str):
        return Path(source).read_bytes()
    if isinstance(source, Path):
        return source.read_bytes()
    return bytes(source)