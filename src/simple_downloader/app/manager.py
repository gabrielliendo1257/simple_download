from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.db import InMemoryRepository
from simple_downloader.domain.event import DownloadStateChangedEvent
from simple_downloader.domain.models import DownloadJob, DownloadRequest, DownloadState
from simple_downloader.domain.protocols import (
    DownloadJobRepository,
    DownloadTask,
)
from simple_downloader.domain.state import can_transition
from simple_downloader.engines import EngineRegistry
from simple_downloader.errors import JobNotFoundError
from simple_downloader.event import EventBus
from simple_downloader.infra.http import describe_http_error


class DownloadManager:
    def __init__(
        self,
        event_bus: EventBus,
        engine_registry: EngineRegistry,
        download_scheduler: DownloadScheduler,
        job_repository: DownloadJobRepository | None = None,
    ) -> None:
        self._jobs: dict[UUID, DownloadJob] = {}
        self._event_bus = event_bus
        self._engines = engine_registry
        self._scheduler = download_scheduler
        self._job_repository = job_repository or InMemoryRepository()

    @property
    def job_repository(self) -> DownloadJobRepository:
        return self._job_repository

    def list(self) -> list[DownloadJob]:
        return list(self._jobs.values())

    def find(self, job_id: UUID) -> DownloadJob | None:
        return self._jobs.get(job_id)

    async def load(self) -> None:
        """Sembra los jobs persistidos (catálogo) al arrancar la app."""
        for job in await self._job_repository.list():
            self._jobs[job.id] = job

    async def remove(self, job_id: UUID) -> None:
        """Descarta el job del catálogo (la UI lo usaba para descartar)."""
        self._jobs.pop(job_id, None)
        await self._job_repository.delete(job_id)

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
        await self._job_repository.save(job)
        await self._event_bus.publish(
            event=DownloadStateChangedEvent(job_id=job.id, state=job.state)
        )

        return job

    async def start(
        self,
        job_id: UUID,
    ) -> None:
        job = self._require_job(job_id)
        if not can_transition(job.state, DownloadState.RUNNING):
            return
        await self._start_task(job)

    async def rename(
        self,
        job_id: UUID,
        title: str,
    ) -> None:
        """Actualiza el título visible del job (p. ej. metadatos del medio)."""
        job = self._require_job(job_id)
        job.request = replace(job.request, title=title)
        await self._job_repository.save(job)
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
            await self._job_repository.save(job)
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

        job.request = replace(job.request, resume=True)
        await self._job_repository.save(job)
        await self._start_task(job)

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
            await self._job_repository.save(job)
            await self._event_bus.publish(
                event=DownloadStateChangedEvent(job_id=job.id, state=job.state)
            )

    async def _start_task(self, job: DownloadJob) -> None:
        """Crea la tarea del job. Los fallos de creación (p. ej. GET de la
        playlist HLS con 403 o sin conexión) se convierten en estado FAILED
        con mensaje legible en lugar de propagarse a la UI."""
        try:
            job.task, job.engine = await self._create_task(job.request)
        except Exception as exc:
            if can_transition(job.state, DownloadState.FAILED):
                job.state = DownloadState.FAILED
                job.error = (
                    describe_http_error(exc) or str(exc) or exc.__class__.__name__
                )
                await self._job_repository.save(job)
                await self._event_bus.publish(
                    event=DownloadStateChangedEvent(job_id=job.id, state=job.state)
                )
            return

        await self._apply_resolved_title(job)
        await self._scheduler.submit(job=job)

    async def _apply_resolved_title(self, job: DownloadJob) -> None:
        """Si la tarea resolvió un título real (p. ej. el nombre del
        documento que reporta Telegram), actualiza la UI, salvo que el
        usuario haya elegido un nombre explícito."""
        resolved = getattr(job.task, "title", None)
        if not resolved or resolved == job.request.title:
            return
        user_named = (
            job.request.output is not None and job.request.output.filename is not None
        )
        if not user_named:
            await self.rename(job.id, resolved)

    async def _create_task(self, request: DownloadRequest) -> tuple[DownloadTask, str]:
        engine = self._engines.engine_for(request.url)
        task = await engine.create_task(request=request)
        return task, engine.name

    def _require_job(self, job_id: UUID) -> DownloadJob:
        job = self._jobs.get(job_id)
        if job is None:
            raise JobNotFoundError(job_id)
        return job
