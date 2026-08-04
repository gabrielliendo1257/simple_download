from __future__ import annotations

from typing import AsyncIterator, Protocol
from uuid import UUID

from simple_downloader.domain.models import (
    DownloadJob,
    DownloadProgress,
    DownloadRequest,
    DownloadResult,
)


class DownloadTask(Protocol):
    """Unidad de trabajo: el scheduler no sabe si es subprocess o nativo."""

    def progress(self) -> AsyncIterator[DownloadProgress]: ...

    async def cancel(self) -> None: ...

    async def pause(self) -> None: ...

    async def finalize(self) -> DownloadResult: ...


class Engine(Protocol):
    """Un Strategy por protocolo de descarga (http directo, hls, yt-dlp...)."""

    name: str

    def supports(self, url: str) -> bool: ...

    async def create_task(self, request: DownloadRequest) -> DownloadTask: ...


class HttpClient(Protocol):
    async def get(self, url: str) -> bytes: ...


class DownloadJobRepository(Protocol):
    async def save(self, job: DownloadJob) -> None: ...

    async def find(self, job_id: UUID) -> DownloadJob | None: ...

    async def list(self) -> list[DownloadJob]: ...