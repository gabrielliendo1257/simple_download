from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.domain.event import DownloadStateChangedEvent
from simple_downloader.domain.models import DownloadJob, DownloadRequest, DownloadState
from simple_downloader.domain.protocols import DownloadTask
from simple_downloader.domain.state import can_transition
from simple_downloader.engines import EngineRegistry
from simple_downloader.errors import JobNotFoundError
from simple_downloader.event import EventBus


class DownloadManager:
    def __init__(
        self,
        event_bus: EventBus,
        engine_registry: EngineRegistry,
        download_scheduler: DownloadScheduler,
    ) -> None:
        self._jobs: dict[UUID, DownloadJob] = {}
        self._event_bus = event_bus
        self._engines = engine_registry
        self._scheduler = download_scheduler

    def list(self) -> list[DownloadJob]:
        return list(self._jobs.values())

    def find(self, job_id: UUID) -> DownloadJob | None:
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
        self._jobs[job.id] = job
        await self._event_bus.publish(
            event=DownloadStateChangedEvent(job_id=job.id, state=job.state)
        )

        return job

    async def start(
        self,
        job_id: UUID,
    ) -> None:
        job = self._require_job(job_id)
        job.task, job.engine = await self._create_task(job.request)
        await self._scheduler.submit(job=job)

    async def rename(
        self,
        job_id: UUID,
        title: str,
    ) -> None:
        """Actualiza el título visible del job (p. ej. metadatos del medio)."""
        job = self._require_job(job_id)
        job.request = replace(job.request, title=title)
        await self._event_bus.publish(
            event=DownloadStateChangedEvent(job_id=job.id, state=job.state)
        )

    async def pause(
        self,
        job_id: UUID,
    ) -> None:
        job = self._require_job(job_id)
        if job.task is not None:
            await job.task.pause()
        if can_transition(job.state, DownloadState.PAUSED):
            job.state = DownloadState.PAUSED
            await self._event_bus.publish(
                event=DownloadStateChangedEvent(job_id=job.id, state=job.state)
            )

    async def resume(
        self,
        job_id: UUID,
    ) -> None:
        job = self._require_job(job_id)
        if not can_transition(job.state, DownloadState.RUNNING):
            return

        request = replace(job.request, resume=True)
        job.task, job.engine = await self._create_task(request)
        await self._scheduler.submit(job=job)

    async def cancel(
        self,
        job_id: UUID,
    ) -> None:
        job = self._require_job(job_id)
        await self._scheduler.cancel_job(job_id=job.id)
        if job.task is not None:
            await job.task.cancel()
        if can_transition(job.state, DownloadState.CANCELLED):
            job.state = DownloadState.CANCELLED
            await self._event_bus.publish(
                event=DownloadStateChangedEvent(job_id=job.id, state=job.state)
            )

    async def _create_task(self, request: DownloadRequest) -> tuple[DownloadTask, str]:
        engine = self._engines.engine_for(request.url)
        task = await engine.create_task(request=request)
        return task, engine.name

    def _require_job(self, job_id: UUID) -> DownloadJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job
