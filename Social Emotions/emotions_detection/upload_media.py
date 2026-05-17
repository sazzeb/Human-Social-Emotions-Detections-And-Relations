from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Callable
import mimetypes

from IPython.display import clear_output, display
from ipywidgets import Button, FileUpload, HTML, Layout, Output, VBox

from .audio_emotions_processing import AudioEmotionsProcessor, TranscriptResult, compact_text, format_timestamp
from .video_emotions_processing import VideoEmotionsProcessor

DEFAULT_UPLOAD_DESCRIPTION = "Choose File"


@dataclass(frozen=True)
class UploadedFileInfo:
    filename: str
    content: bytes
    media_kind: str
    size: int


def _to_bytes(content: Any) -> bytes:
    if isinstance(content, bytes):
        return content
    if isinstance(content, bytearray):
        return bytes(content)
    if isinstance(content, memoryview):
        return content.tobytes()
    if isinstance(content, str):
        return content.encode("utf-8")
    return bytes(content)


def detect_media_kind(filename: str, content: Any) -> str:
    head = _to_bytes(content)[:64]
    lower_name = filename.lower()

    image_signatures = (
        (b"\x89PNG\r\n\x1a\n", "image"),
        (b"\xff\xd8\xff", "image"),
        (b"GIF87a", "image"),
        (b"GIF89a", "image"),
        (b"BM", "image"),
        (b"RIFF", "image"),
    )
    for signature, file_type in image_signatures:
        if head.startswith(signature):
            if signature == b"RIFF" and b"WEBP" not in head:
                continue
            return file_type

    audio_signatures = (
        (b"ID3", "audio"),
        (b"\xff\xfb", "audio"),
        (b"\xff\xf3", "audio"),
        (b"\xff\xf2", "audio"),
        (b"RIFF", "audio"),
        (b"fLaC", "audio"),
        (b"OggS", "audio"),
    )
    for signature, file_type in audio_signatures:
        if head.startswith(signature):
            if signature == b"RIFF" and b"WAVE" not in head:
                continue
            return file_type

    if b"ftyp" in head[:16]:
        return "video"
    if lower_name.endswith((".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm")):
        return "video"

    if lower_name.endswith((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".ico")):
        return "image"
    if lower_name.endswith((".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma")):
        return "audio"

    try:
        decoded = _to_bytes(content).decode("utf-8")
        if decoded.strip():
            printable_ratio = sum(ch.isprintable() or ch.isspace() for ch in decoded) / max(len(decoded), 1)
            if printable_ratio > 0.95:
                return "text"
    except UnicodeDecodeError:
        pass

    guessed_type, _ = mimetypes.guess_type(filename)
    if guessed_type:
        return guessed_type.split("/", 1)[0]

    return "unknown"


def normalize_uploaded_items(uploaded_value: Any) -> list[dict[str, Any]]:
    if not uploaded_value:
        return []
    if isinstance(uploaded_value, dict):
        return list(uploaded_value.values())
    try:
        return list(uploaded_value)
    except TypeError:
        return [uploaded_value]


def summarize_uploaded_file(uploaded_file: dict[str, Any]) -> UploadedFileInfo:
    filename = uploaded_file["name"]
    content = _to_bytes(uploaded_file["content"])
    return UploadedFileInfo(
        filename=filename,
        content=content,
        media_kind=detect_media_kind(filename, content),
        size=len(content),
    )


def _segment_row(segment) -> HTML:
    return HTML(
        f"<div style='display:grid;grid-template-columns:7.5rem 1fr;gap:0.75rem;align-items:start;padding:0.45rem 0;border-bottom:1px solid #e7e9ee;'>"
        f"<div style='font-size:0.8rem;font-weight:700;color:#4c5563;white-space:nowrap;'>{escape(segment.timestamp_label)}</div>"
        f"<div style='line-height:1.5;white-space:pre-wrap;'>{escape(segment.text)}</div>"
        f"</div>"
    )


def _segment_empty_row() -> HTML:
    return HTML("<div style='padding:0.5rem 0;color:#5b6472;'>No speech transcript was detected.</div>")


class SegmentTranscriptRow(VBox):
    def __init__(self, segment) -> None:
        self.segment = segment
        self.is_expanded = False
        self.toggle_button = Button(
            description=self._button_label(),
            layout=Layout(width="100%", justify_content="flex-start"),
        )
        self.toggle_button.style.button_color = "#ffffff"
        self.toggle_button.style.font_weight = "500"
        self.toggle_button.on_click(self._toggle)

        self.detail_box = VBox(
            [
                HTML(
                    "<div style='padding:0.45rem 0.25rem 0.15rem 0.25rem;color:#4c5563;font-size:0.82rem;font-weight:700;'>"
                    f"{escape(segment.timestamp_label)}"
                    "</div>"
                ),
                HTML(
                    f"<div style='padding:0 0.25rem 0.65rem 0.25rem;line-height:1.5;white-space:pre-wrap;'>{escape(segment.text)}</div>"
                ),
            ],
            layout=Layout(display="none", padding="0 0 0 1rem"),
        )
        super().__init__([self.toggle_button, self.detail_box])

    def _button_label(self) -> str:
        prefix = "▼" if self.is_expanded else "▶"
        return f"{prefix} {self.segment.timestamp_label} | {compact_text(self.segment.text, 90)}"

    def _toggle(self, _button: Button) -> None:
        self.is_expanded = not self.is_expanded
        self.toggle_button.description = self._button_label()
        self.detail_box.layout.display = "flex" if self.is_expanded else "none"


class FileTranscriptRow(VBox):
    def __init__(self, result: TranscriptResult) -> None:
        self.result = result
        self.is_expanded = False
        self.toggle_button = Button(
            description=self._button_label(),
            layout=Layout(width="100%", justify_content="flex-start"),
        )
        self.toggle_button.style.button_color = "#f4f6f8"
        self.toggle_button.style.font_weight = "600"
        self.toggle_button.on_click(self._toggle)

        transcript_rows = [SegmentTranscriptRow(segment) for segment in result.segments] or [_segment_empty_row()]
        self.detail_box = VBox(
            [
                HTML(
                    "<div style='padding:0.5rem 0.25rem 0.25rem 0.25rem;color:#5b6472;font-size:0.85rem;'>"
                    f"Media: {escape(result.media_kind)} | Segments: {len(result.segments)}"
                    "</div>"
                ),
                *transcript_rows,
            ],
            layout=Layout(display="none", padding="0 0 0 1rem"),
        )
        super().__init__([self.toggle_button, self.detail_box])

    def _button_label(self) -> str:
        prefix = "▼" if self.is_expanded else "▶"
        return f"{prefix} {self.result.source_name} | {self.result.media_kind}"

    def _toggle(self, _button: Button) -> None:
        self.is_expanded = not self.is_expanded
        self.toggle_button.description = self._button_label()
        self.detail_box.layout.display = "flex" if self.is_expanded else "none"


def build_result_widget(result: TranscriptResult) -> FileTranscriptRow:
    return FileTranscriptRow(result)


def build_results_accordion(results: list[TranscriptResult]) -> VBox:
    if not results:
        return VBox([HTML("<div style='color:#5b6472;'>Upload audio or video files to see transcripts here.</div>")])
    return VBox([build_result_widget(result) for result in results], layout=Layout(width="100%"))


class MediaUploadSession:
    def __init__(
        self,
        description: str = DEFAULT_UPLOAD_DESCRIPTION,
        multiple: bool = True,
        accept: str = "audio/*,video/*",
        audio_processor: AudioEmotionsProcessor | None = None,
        video_processor: VideoEmotionsProcessor | None = None,
        on_result: Callable[[TranscriptResult], None] | None = None,
    ) -> None:
        self.output = Output()
        self.uploader = FileUpload(accept=accept, multiple=multiple, description=description)
        self.audio_processor = audio_processor or AudioEmotionsProcessor()
        self.video_processor = video_processor or VideoEmotionsProcessor(audio_processor=self.audio_processor)
        self.on_result = on_result
        self.results: list[TranscriptResult] = []
        self.uploader.observe(self._handle_upload_change, names="value")

    def _process_uploaded_file(self, uploaded_file: dict[str, Any]) -> TranscriptResult:
        info = summarize_uploaded_file(uploaded_file)
        if info.media_kind == "video":
            return self.video_processor.transcribe(info.content, filename=info.filename)
        if info.media_kind == "audio":
            return self.audio_processor.transcribe(info.content, filename=info.filename)
        raise ValueError(f"Unsupported file type for transcription: {info.filename} ({info.media_kind})")

    def _handle_upload_change(self, change: dict[str, Any]) -> None:
        if not self.uploader.value:
            return

        with self.output:
            clear_output()
            self.results = []
            for uploaded_file in normalize_uploaded_items(self.uploader.value):
                result = self._process_uploaded_file(uploaded_file)
                self.results.append(result)
                if self.on_result is not None:
                    self.on_result(result)

            display(build_results_accordion(self.results))

    def display(self) -> MediaUploadSession:
        display(self.uploader, self.output)
        return self


def create_upload_session(
    description: str = DEFAULT_UPLOAD_DESCRIPTION,
    multiple: bool = True,
    accept: str = "audio/*,video/*",
    audio_processor: AudioEmotionsProcessor | None = None,
    video_processor: VideoEmotionsProcessor | None = None,
    on_result: Callable[[TranscriptResult], None] | None = None,
) -> MediaUploadSession:
    return MediaUploadSession(
        description=description,
        multiple=multiple,
        accept=accept,
        audio_processor=audio_processor,
        video_processor=video_processor,
        on_result=on_result,
    )