from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from simple_downloader.app.manager import DownloadManager
from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.domain.event import (
    DownloadProgressEvent,
    DownloadStateChangedEvent,
)
from simple_downloader.domain.models import (
    DownloadProgress,
    DownloadRequest,
    DownloadResult,
    DownloadState,
)
from simple_downloader.domain.protocols import DownloadTask
from simple_downloader.engines import EngineRegistry
from simple_downloader.errors import JobNotFoundError
from simple_downloader.event import EventBus


class FakeTask(DownloadTask):
    def __init__(
        self,
        *,
        exit_code: int = 0,
        steps: int = 3,
        pause_flag: asyncio.Event | None = None,
    ) -> None:
        self.exit_code = exit_code
        self.steps = steps
        self.pause_flag = pause_flag
        self.cancelled = False
        self.paused = False

    async def progress(self):
        for step in range(1, self.steps + 1):
            if self.pause_flag is not None:
                await self.pause_flag.wait()
            if self.cancelled:
                break
            yield DownloadProgress(downloaded_bytes=step, total_bytes=self.steps)

    async def finalize(self) -> DownloadResult:
        return DownloadResult(exit_code=self.exit_code)

    async def cancel(self) -> None:
        self.cancelled = True

    async def pause(self) -> None:
        self.paused = True


class FakeEngine:
    name = "fake"

    def __init__(self, task: FakeTask | None = None) -> None:
        self._task = task or FakeTask()

    def supports(self, url: str) -> bool:
        return True

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        return self._task


def _build(task: FakeTask | None = None) -> tuple[DownloadManager, DownloadScheduler, EventBus]:
    bus = EventBus()
    engine = FakeEngine(task)
    registry = EngineRegistry()
    registry.register(engine)
    scheduler = DownloadScheduler(
        bus,
        max_workers=3,
    )
    scheduler.start()
    manager = DownloadManager(
        event_bus=bus,
        engine_registry=registry,
        download_scheduler=scheduler,
    )
    return manager, scheduler, bus


async def test_enqueue_creates_queued_job() -> None:
    manager, scheduler, _bus = _build()
    job = await manager.enqueue(DownloadRequest(url="https://x/file.mp4"))

    assert job.state is DownloadState.QUEUED
    assert manager.find(job.id) is job
    assert len(manager.list()) == 1

    await scheduler.finish()


async def test_start_completes_job() -> None:
    manager, scheduler, _bus = _build()
    job = await manager.enqueue(DownloadRequest(url="https://x/file.mp4"))

    await manager.start(job.id)
    await asyncio.sleep(0.05)
    await scheduler.finish()

    assert job.state is DownloadState.COMPLETED


async def test_failing_task_marks_failed() -> None:
    manager, scheduler, _bus = _build(task=FakeTask(exit_code=2))
    job = await manager.enqueue(DownloadRequest(url="https://x/file.mp4"))

    await manager.start(job.id)
    await asyncio.sleep(0.05)
    await scheduler.finish()

    assert job.state is DownloadState.FAILED


async def test_pause_then_resume_completes() -> None:
    pause_flag = asyncio.Event()
    task = FakeTask(steps=10, pause_flag=pause_flag)
    manager, scheduler, _bus = _build(task)

    job = await manager.enqueue(DownloadRequest(url="https://x/file.mp4"))
    await manager.start(job.id)
    await asyncio.sleep(0.05)
    assert job.state is DownloadState.RUNNING

    await manager.pause(job.id)
    assert job.state is DownloadState.PAUSED
    assert task.paused

    await manager.resume(job.id)
    assert job.state is DownloadState.RUNNING

    pause_flag.set()
    await asyncio.sleep(0.1)
    await scheduler.finish()
    assert job.state is DownloadState.COMPLETED


async def test_cancel_marks_cancelled() -> None:
    manager, scheduler, _bus = _build()
    job = await manager.enqueue(DownloadRequest(url="https://x/file.mp4"))

    await manager.cancel(job.id)
    assert job.state is DownloadState.CANCELLED
    await scheduler.finish()


async def test_unknown_job_raises() -> None:
    manager, scheduler, _bus = _build()

    with pytest.raises(JobNotFoundError):
        await manager.start(uuid4())

    await scheduler.finish()


async def test_progress_events_published() -> None:
    bus = EventBus()
    progress: list[int] = []
    bus.subscribe(
        DownloadProgressEvent, lambda e: progress.append(e.progress.downloaded_bytes)
    )

    engine = FakeEngine()
    registry = EngineRegistry()
    registry.register(engine)
    scheduler = DownloadScheduler(bus)
    scheduler.start()
    manager = DownloadManager(
        event_bus=bus,
        engine_registry=registry,
        download_scheduler=scheduler,
    )

    job = await manager.enqueue(DownloadRequest(url="https://x/file.mp4"))
    await manager.start(job.id)
    await asyncio.sleep(0.05)
    await scheduler.finish()

    assert progress == [1, 2, 3]


async def test_state_change_events_published() -> None:
    bus = EventBus()
    states: list[DownloadState] = []
    bus.subscribe(DownloadStateChangedEvent, lambda e: states.append(e.state))

    engine = FakeEngine()
    registry = EngineRegistry()
    registry.register(engine)
    scheduler = DownloadScheduler(bus)
    scheduler.start()
    manager = DownloadManager(
        event_bus=bus,
        engine_registry=registry,
        download_scheduler=scheduler,
    )

    job = await manager.enqueue(DownloadRequest(url="https://x/file.mp4"))
    await manager.start(job.id)
    await asyncio.sleep(0.05)
    await scheduler.finish()

    assert states == [DownloadState.QUEUED, DownloadState.RUNNING, DownloadState.COMPLETED]


def test_download_request_immutability() -> None:
    request = DownloadRequest(url="https://x/file.mp4")
    with pytest.raises(Exception):
        request.url = "other"