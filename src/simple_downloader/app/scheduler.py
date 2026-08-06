import asyncio
from uuid import UUID

from simple_downloader.domain.event import (
    DownloadProgressEvent,
    DownloadStateChangedEvent,
)
from simple_downloader.domain.models import (
    DownloadJob,
    DownloadResult,
    DownloadState,
)
from simple_downloader.domain.state import can_transition
from simple_downloader.event import EventBus
from simple_downloader.infra.http import describe_http_error

_STOP = object()


class DownloadScheduler:
    def __init__(self, event_bus: EventBus, max_workers: int = 3) -> None:
        self._event_bus = event_bus
        self._max_workers = max_workers
        self._queue: asyncio.Queue[DownloadJob | object] = asyncio.Queue()
        self._running: dict[UUID, asyncio.Task] = {}

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
                await self._event_bus.publish(_state_event(job))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if can_transition(job.state, DownloadState.FAILED):
                job.state = DownloadState.FAILED
                job.error = (
                    describe_http_error(exc) or str(exc) or exc.__class__.__name__
                )
                await self._event_bus.publish(_state_event(job))


def _failure_message(result: DownloadResult) -> str:
    if result.stderr:
        return result.stderr.strip()
    return f"el proceso salió con código {result.exit_code}"


def _state_event(job: DownloadJob) -> DownloadStateChangedEvent:
    return DownloadStateChangedEvent(job_id=job.id, state=job.state)
