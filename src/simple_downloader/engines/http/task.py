from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

from simple_downloader.domain.models import DownloadProgress, DownloadResult
from simple_downloader.domain.protocols import HttpClient
from simple_downloader.engines.common import (
    discard_partial,
    resume_plan,
    save_resume_meta,
)


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
        self.resume_fallback: bool = False
        self.resume_fallback_reason: str | None = None

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
        """Descarga (o reanuda) con Range. Si el servidor ignora el rango
        (200), se reinicia desde cero y se avisa al usuario."""
        plan = resume_plan(self.out_file, url=self.url)
        if not plan.valid:
            self.resume_fallback = True
            self.resume_fallback_reason = f"{plan.reason}; se reinició la descarga"
        offset = plan.offset if plan.valid else 0

        stream = self.http.stream(self.url, offset=offset)
        try:
            status, total, _ = await anext(stream)
            if total is not None:
                self._total = total
            save_resume_meta(self.out_file, url=self.url, total_bytes=self._total)

            if status == 416:
                # El servidor dice que el rango no aplica: ya está completo.
                await stream.aclose()
                self._written = self._total or 0
                self._change.set()
                return

            if status == 206 and offset > 0:
                mode = "ab"  # el servidor respetó el rango
            else:
                if status == 200 and offset > 0:
                    # Sin soporte de Range: descartar el parcial y reiniciar.
                    self.resume_fallback = True
                    self.resume_fallback_reason = (
                        "el servidor ignoró el rango (HTTP 200); "
                        "se reinició la descarga"
                    )
                mode = "wb"
                offset = 0

            with open(self.out_file, mode) as handle:
                written_here = 0
                async for _status, _total, chunk in stream:
                    if _total is not None:
                        self._total = _total
                    if chunk:
                        handle.write(chunk)
                        written_here += len(chunk)
                        self._written = offset + written_here
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
        # Cancelar abandona la descarga: el parcial no se conserva.
        await self.pause()
        discard_partial(self.out_file)
