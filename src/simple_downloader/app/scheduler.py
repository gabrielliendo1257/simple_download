import asyncio
from uuid import UUID

from simple_downloader.event import EventBus
from simple_downloader.models import DownloadJob, DownloadState
from simple_downloader.process import DownloadProgressEvent


class DownloadScheduler:
    def __init__(self, event_bus: EventBus, max_workers: int = 3) -> None:
        self.queue = asyncio.Queue[DownloadJob]()
        self.running: dict[UUID, asyncio.Task] = {}
        self.max_workers = max_workers
        self._event_bus: EventBus = event_bus

    async def _run(
        self,
        job: DownloadJob,
    ):
        assert job.process is not None, "process() is None"

        async for progress in job.process.progress():
            job.progress = progress

            await self._event_bus.publish(
                event=DownloadProgressEvent(
                    job.id,
                    progress,
                )
            )

        result = await job.process.wait()

        if result.exit_code == 0:
            job.state = DownloadState.COMPLETED
        else:
            job.state = DownloadState.FAILED
            print("Failed: ", result.stderr)

    async def start(self):
        workers = [
            asyncio.create_task(self.__worker()) for _ in range(self.max_workers)
        ]

        asyncio.gather(*workers)

    async def down(self):
        for _ in range(self.max_workers):
            await self.queue.put(None)

    async def __worker(self):
        while True:
            job = await self.queue.get()

            task = asyncio.create_task(self._run(job))

            self.running[job.id] = task

            task.add_done_callback(lambda t: self.running.pop(job.id, None))

    async def submit(self, job: DownloadJob):
        job.state = DownloadState.QUEUED

        await self.queue.put(job)

    async def pause(self, job: DownloadJob):
        if job.process:
            await job.process.terminate()

        job.state = DownloadState.PAUSED

    async def resume(self, job: DownloadJob):
        await self.submit(job=job)
