from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from uuid import UUID, uuid4

from simple_downloader.db import DownloadJobRepository
from simple_downloader.event import DownloadProgressEvent, EventBus
from simple_downloader.executor import ExecutableName
from simple_downloader.process import DownloadProgress, RunningProcess
from simple_downloader.sources import SourceProvider


@dataclass(frozen=True)
class DownloadRequest:
    url: str
    output: Path
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


class DownloadManager:

    def __init__(
        self,
        repository: DownloadJobRepository,
        event_bus: EventBus,
        source_provider: SourceProvider
    ):
        self._semaphore = asyncio.Semaphore(3)
        self._jobs: dict[UUID, DownloadJob]
        self._event_bus = event_bus
        self._job_repository = repository
        self._source_provider = source_provider

    def _find_job(self, job_id: UUID) -> DownloadJob:
        return self._jobs.get(job_id)

    async def _monitor(
        self,
        job: DownloadJob,
    ):

        async for progress in job.process.progress():

            job.progress = progress

            await self._event_bus.publish(
                DownloadProgressEvent(
                    job.id,
                    progress,
                )
            )

        result = await job.process.wait()

        if result.exit_code == 0:
            job.state = DownloadState.COMPLETED
        else:
            job.state = DownloadState.FAILED

    async def enqueue(
        self,
        request: DownloadRequest,
    ) -> DownloadJob:
        job = DownloadJob(
            id=uuid4(),
            request=request,
            state=DownloadState.QUEUED,
        )

        self._jobs[job.id] = job

        return job

    async def start(
        self,
        job_id: UUID,
    ): ...

    async def pause(
        self,
        job_id: UUID,
    ):
        job = self._find_job(job_id=job_id)
        await job.process.terminate()

    async def resume(
        self,
        job_id: UUID,
    ):
        job = self._find_job(job_id=job_id)
        source = self._source_provider.get_source(executable_name=ExecutableName.YT_DLP)

        process: RunningProcess = await source.download(url=job.request.url, output=job.request.output, format_id=job.request.format)

    async def cancel(
        self,
        job_id: UUID,
    ): ...

    async def remove(
        self,
        job_id: UUID,
    ): ...

    def get(
        self,
        job_id: UUID,
    ) -> DownloadJob: ...

    def list(
        self,
    ) -> list[DownloadJob]: ...
