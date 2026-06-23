from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from simple_downloader.process import DownloadProgress, RunningProcess


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    output: Path | None = None
    format: str | None = None
    extract_audio: bool = False
    audio_format: str | None = None
    subtitles: bool = False


@dataclass
class DownloadJob:
    id: UUID
    request: DownloadRequest
    state: DownloadState
    progress: DownloadProgress | None = None
    process: RunningProcess | None = None


class DownloadState(Enum):
    QUEUED = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()
