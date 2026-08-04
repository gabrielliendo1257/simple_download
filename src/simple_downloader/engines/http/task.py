from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from simple_downloader.domain.models import DownloadProgress, DownloadResult
from simple_downloader.domain.protocols import HttpClient


@dataclass
class HttpDownloadTask:
    url: str
    out_file: Path
    http: HttpClient

    def __post_init__(self) -> None:
        self._started = False
        self._sink: asyncio.Queue[bytes | None] | None = None
        self._change: asyncio.Event | None = None
        self._done: asyncio.Event | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._written: int = 0
        self._total: int | None = None

    def _start(self) -> asyncio.Task[None]:
        if not self._started:
            self._started = True
            self._sink = asyncio.Queue(maxsize=32)
            self._change = asyncio.Event()
            self._done = asyncio.Event()

        if self._run_task is None:
            self._run_task = asyncio.create_task(self._run())

        return self._run_task

    async def _run(self) -> None:
        try:
            with open(self.out_file, "wb") as handle:
                async for total, chunk in self.http.stream(self.url):
                    if total is not None:
                        self._total = total
                    if chunk:
                        handle.write(chunk)
                        self._written += len(chunk)
                        self._change.set()
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
                yield DownloadProgress(
                    downloaded_bytes=written, total_bytes=self._total
                )

        await run_task

    async def finalize(self) -> DownloadResult:
        await self._start()
        return DownloadResult(exit_code=0, output=self.out_file)

    async def pause(self) -> None:
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass

    async def cancel(self) -> None:
        await self.pause()
