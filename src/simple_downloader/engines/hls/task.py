from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Sequence

from simple_downloader.domain.models import DownloadProgress, DownloadResult

from simple_downloader.engines.hls.fetch import SegmentFetcher
from simple_downloader.engines.hls.models import Segment


class SegmentDownloadError(RuntimeError):
    def __init__(self, index: int) -> None:
        super().__init__(f"segment {index} failed to download")
        self.index = index


@dataclass
class HlsTask:
    out_file: Path
    fetcher: SegmentFetcher
    segments: Sequence[Segment]
    max_parallel: int = 6
    init_uri: str | None = None

    def __post_init__(self) -> None:
        self._started = False
        self._paused = False
        self._sem: asyncio.Semaphore | None = None
        self._sink: asyncio.Queue[tuple[int, bytes | None]] | None = None
        self._change: asyncio.Event | None = None
        self._done: asyncio.Event | None = None
        self._workers: list[asyncio.Task[None]] = []
        self._written: int = 0
        self._run_task: asyncio.Task[None] | None = None

    def _start(self) -> asyncio.Task[None]:
        if not self._started:
            self._started = True
            self._sem = asyncio.Semaphore(self.max_parallel)
            self._sink = asyncio.Queue(maxsize=self.max_parallel * 2)
            self._change = asyncio.Event()
            self._done = asyncio.Event()

        if self._run_task is None:
            self._run_task = asyncio.create_task(self._run())

        return self._run_task

    async def _worker(self, segment: Segment) -> None:
        async with self._sem:
            try:
                data = await self.fetcher.fetch(segment)
            except asyncio.CancelledError:
                raise
            except Exception:
                await self._sink.put((segment.index, None))
                raise

            await self._sink.put((segment.index, data))

    async def _run(self) -> None:
        segments = list(self.segments)
        if not segments:
            self._done.set()
            return

        self._workers = [
            asyncio.create_task(self._worker(segment)) for segment in segments
        ]

        pending: dict[int, bytes] = {}
        expected = 0

        with open(self.out_file, "wb") as handle:
            try:
                if self.init_uri is not None:
                    handle.write(await self.fetcher.fetch_init(self.init_uri))

                while expected < len(segments):
                    index, data = await self._sink.get()
                    if data is None:
                        raise SegmentDownloadError(index)

                    pending[index] = data

                    while expected < len(segments):
                        chunk = pending.pop(expected, None)
                        if chunk is None:
                            break
                        handle.write(chunk)
                        self._written += len(chunk)
                        self._change.set()
                        expected += 1

                await asyncio.gather(*self._workers)
            except asyncio.CancelledError:
                for worker in self._workers:
                    worker.cancel()
                raise
            finally:
                self._done.set()

    async def progress(self) -> AsyncIterator[DownloadProgress]:
        run_task = self._start()
        written = 0

        while not self._done.is_set():
            done_waiter = asyncio.create_task(self._done.wait())
            change_waiter = asyncio.create_task(self._change.wait())
            await asyncio.wait(
                {done_waiter, change_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            change_waiter.cancel()
            done_waiter.cancel()

            self._change.clear()
            if self._written != written:
                written = self._written
                yield DownloadProgress(downloaded_bytes=written, total_bytes=None)

        await run_task

    async def finalize(self) -> DownloadResult:
        await self._start()
        return DownloadResult(exit_code=0, output=self.out_file)

    async def pause(self) -> None:
        self._paused = True
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass

    async def cancel(self) -> None:
        await self.pause()