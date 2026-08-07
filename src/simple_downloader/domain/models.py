from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from simple_downloader.domain.state import DownloadState

if TYPE_CHECKING:
    from simple_downloader.domain.protocols import DownloadTask


@dataclass(frozen=True)
class DownloadOutput:
    """Configuración de salida definida por el usuario.

    Reglas de resolución (ver engines/common.resolve_output):
    1. filename presente -> directory / filename
    2. template presente -> placeholders reemplazados con la info del medio
    3. ninguno -> el engine decide (basename de la URL, uuid, etc.)
    """

    directory: Path = Path("downloads")
    filename: str | None = None
    template: str | None = None
    overwrite: bool = False
    create_directories: bool = True


@dataclass(frozen=True)
class DownloadContext:
    """Contexto HTTP/descarga por request, configurable por el usuario.

    Si referer es None, los engines lo derivan del origin de la URL.
    """

    referer: str | None = None
    user_agent: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_sec: float = 30.0
    max_parallel_segments: int = 6  # solo aplica a HLS


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    title: str | None = None
    output: DownloadOutput | None = None
    context: DownloadContext | None = None
    format_id: str | None = None
    extract_audio: bool = False
    audio_format: str | None = None
    subtitles: bool = False
    resume: bool = False


@dataclass(frozen=True)
class MediaInfo:
    id: str
    title: str
    webpage_url: str
    uploader: str | None = None
    duration: int | None = None


@dataclass(frozen=True)
class Format:
    format_id: str
    ext: str
    resolution: str
    filesize_approx: int | None = None


@dataclass(frozen=True)
class DownloadProgress:
    downloaded_bytes: int
    total_bytes: int | None = None
    speed_bps: float | None = None
    segments_done: int | None = None
    segments_total: int | None = None


@dataclass(frozen=True)
class DownloadResult:
    exit_code: int
    output: Path | None = None
    stderr: str | None = None


@dataclass
class DownloadJob:
    id: UUID
    request: DownloadRequest
    state: DownloadState
    task: DownloadTask | None = None
    progress: DownloadProgress | None = None
    engine: str | None = None
    error: str | None = None
    notice: str | None = None
