from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.errors import JobNotFoundError
from simple_downloader.event import EventBus
from simple_downloader.executor import ExecutableName
from simple_downloader.models import DownloadJob, DownloadRequest, DownloadState
from simple_downloader.process import (
    RunningProcess,
)
from simple_downloader.sources import SourceProvider


class DownloadManager:
    def __init__(
        self,
        event_bus: EventBus,
        source_provider: SourceProvider,
        download_scheduler: DownloadScheduler,
        max_workers: int = 3,
    ):
        self._jobs: dict[UUID, DownloadJob] = {}
        self._event_bus = event_bus
        self._source_provider = source_provider
        self._download_scheduler = download_scheduler

    def _find_job(self, job_id: UUID) -> DownloadJob | None:
        return self._jobs.get(job_id)

    async def enqueue(
        self,
        request: DownloadRequest,
    ) -> DownloadJob:
        job = DownloadJob(
            id=uuid4(),
            request=request,
            state=DownloadState.QUEUED,
        )
        print("Download ENQUEUE job_id - ", job.id)
        self._jobs[job.id] = job

        return job

    async def start(
        self,
        job_id: UUID,
    ) -> None:
        print("Download START job_id - ", job_id)
        job = self._find_job(job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id=job_id)
        source = self._source_provider.get_source(executable_name=ExecutableName.YT_DLP)

        runner: RunningProcess = await source.download(
            url=job.request.url,
            output=job.request.output,
            format_id=job.request.format,
            extract_audio=job.request.extract_audio,
        )
        job.process = runner
        await asyncio.create_task(self._download_scheduler.submit(job=job))

    async def pause(
        self,
        job_id: UUID,
    ) -> None:
        print("Download PAUSED job_id - ", job_id)
        job = self._find_job(job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id=job_id)
        assert job.process is not None, "process not found"
        await self._download_scheduler.pause(job=job)

    async def resume(
        self,
        job_id: UUID,
    ) -> None:
        print("Download RESUME job:id - ", job_id)
        job = self._find_job(job_id=job_id)
        if job is None:
            raise JobNotFoundError(job_id=job_id)
        source = self._source_provider.get_source(executable_name=ExecutableName.YT_DLP)

        runner: RunningProcess = await source.download(
            url=job.request.url,
            output=job.request.output,
            extract_audio=job.request.extract_audio,
            format_id=job.request.format,
            resume=True,
        )
        job.process = runner
        await asyncio.create_task(self._download_scheduler.submit(job=job))
