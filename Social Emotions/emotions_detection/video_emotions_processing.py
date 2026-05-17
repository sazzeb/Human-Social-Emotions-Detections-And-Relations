from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import tempfile

from imageio_ffmpeg import get_ffmpeg_exe

from .audio_emotions_processing import AudioEmotionsProcessor, TranscriptResult, _materialize_source


class VideoEmotionsProcessor:
    def __init__(self, audio_processor: AudioEmotionsProcessor | None = None) -> None:
        self.audio_processor = audio_processor or AudioEmotionsProcessor()

    def _extract_audio(self, source_path: Path) -> tuple[Path, callable]:
        ffmpeg_path = Path(get_ffmpeg_exe())
        output_handle = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        output_handle.close()
        output_path = Path(output_handle.name)
        command = [
            str(ffmpeg_path),
            "-y",
            "-i",
            str(source_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-acodec",
            "pcm_s16le",
            str(output_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True)
        if completed.returncode != 0:
            output_path.unlink(missing_ok=True)
            raise RuntimeError(
                "Video audio extraction failed: "
                f"{completed.stderr.strip() or completed.stdout.strip() or 'ffmpeg exited with a non-zero status.'}"
            )
        return output_path, lambda: output_path.unlink(missing_ok=True)

    def transcribe(
        self,
        source: str | Path | bytes | bytearray | memoryview,
        filename: str | None = None,
    ) -> TranscriptResult:
        source_path, cleanup_source = _materialize_source(source, filename)
        try:
            audio_path, cleanup_audio = self._extract_audio(source_path)
            try:
                return self.audio_processor.transcribe_file(
                    audio_path,
                    filename=filename or source_path.name,
                    media_kind="video",
                )
            finally:
                cleanup_audio()
        finally:
            cleanup_source()