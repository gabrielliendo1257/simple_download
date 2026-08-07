import asyncio
from uuid import UUID

from simple_downloader.db import InMemoryRepository
from simple_downloader.domain.event import (
    DownloadProgressEvent,
    DownloadStateChangedEvent,
)
from simple_downloader.domain.models import (
    DownloadJob,
    DownloadResult,
    DownloadState,
)
from simple_downloader.domain.protocols import DownloadJobRepository
from simple_downloader.domain.state import can_transition
from simple_downloader.event import EventBus
from simple_downloader.infra.http import describe_http_error

_STOP = object()


class DownloadScheduler:
    def __init__(
        self,
        event_bus: EventBus,
        max_workers: int = 3,
        job_repository: DownloadJobRepository | None = None,
    ) -> None:
        self._event_bus = event_bus
        self._max_workers = max_workers
        self._queue: asyncio.Queue[DownloadJob | object] = asyncio.Queue()
        self._running: dict[UUID, asyncio.Task] = {}
        self._job_repository = job_repository or InMemoryRepository()

    def start(self) -> None:
        for _ in range(self._max_workers):
            asyncio.create_task(self._worker())

    async def finish(self) -> None:
        for _ in range(self._max_workers):
            await self._queue.put(_STOP)

    async def submit(self, job: DownloadJob) -> None:
        await self._queue.put(job)
        if can_transition(job.state, DownloadState.RUNNING):
            job.state = DownloadState.RUNNING
            await self._event_bus.publish(_state_event(job))

    async def cancel_job(self, job_id: UUID) -> None:
        run_task = self._running.get(job_id)
        if run_task is not None and not run_task.done():
            run_task.cancel()

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            if item is _STOP:
                return
            job = item
            task = asyncio.create_task(self._run(job))
            self._running[job.id] = task
            task.add_done_callback(lambda _t, _id=job.id: self._running.pop(_id, None))

    async def _run(self, job: DownloadJob) -> None:
        assert job.task is not None, "job.task is None"

        try:
            async for progress in job.task.progress():
                job.progress = progress
                if job.state is not DownloadState.RUNNING:
                    continue
                self._carry_resume_notice(job)
                self._carry_waiting_notice(job)
                await self._event_bus.publish(
                    event=DownloadProgressEvent(job_id=job.id, progress=progress)
                )

            result = await job.task.finalize()

            target = (
                DownloadState.COMPLETED
                if result.exit_code == 0
                else DownloadState.FAILED
            )
            if can_transition(job.state, target):
                job.state = target
                if target is DownloadState.FAILED:
                    job.error = _failure_message(result)
                self._carry_resume_notice(job)
                await self._job_repository.save(job)
                await self._event_bus.publish(_state_event(job))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if can_transition(job.state, DownloadState.FAILED):
                job.state = DownloadState.FAILED
                job.error = (
                    describe_http_error(exc) or str(exc) or exc.__class__.__name__
                )
                self._carry_resume_notice(job)
                await self._job_repository.save(job)
                await self._event_bus.publish(_state_event(job))

    def _carry_resume_notice(self, job: DownloadJob) -> None:
        """Si la tarea tuvo que reiniciar desde cero (servidor sin soporte
        de reanudación), deja el aviso en el job para la UI."""
        task = job.task
        if task is None or job.notice is not None:
            return
        if getattr(task, "resume_fallback", False):
            job.notice = (
                getattr(task, "resume_fallback_reason", None)
                or "no se pudo reanudar; se reinició la descarga"
            )

    def _carry_waiting_notice(self, job: DownloadJob) -> None:
        """Avisa (y luego limpia) cuando la descarga espera turno por el
        límite de conexiones de Telegram: el job sigue en RUNNING con
        0 bytes, pero el usuario ve el motivo en la lista."""
        task = job.task
        if task is None:
            return
        waiting = getattr(task, "waiting_for_slot", False)
        if waiting and job.notice is None:
            job.notice = (
                "esperando turno de Telegram (máx. 2 descargas " "simultáneas)…"
            )
        elif (
            not waiting
            and job.notice is not None
            and job.notice.startswith("esperando turno de Telegram")
        ):
            job.notice = None


def _failure_message(result: DownloadResult) -> str:
    if result.stderr:
        return result.stderr.strip()
    return f"el proceso salió con código {result.exit_code}"


def _state_event(job: DownloadJob) -> DownloadStateChangedEvent:
    return DownloadStateChangedEvent(job_id=job.id, state=job.state)
