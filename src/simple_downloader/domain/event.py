from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from simple_downloader.domain.models import DownloadProgress, DownloadState


@dataclass(frozen=True)
class DownloadProgressEvent:
    job_id: UUID
    progress: DownloadProgress


@dataclass(frozen=True)
class DownloadStateChangedEvent:
    job_id: UUID
    state: DownloadState