from __future__ import annotations

from typing import AsyncIterator

from simple_downloader.domain.models import DownloadProgress, DownloadResult
from simple_downloader.domain.protocols import DownloadTask
from simple_downloader.process import RunningProcess


class SubprocessTaskAdapter(DownloadTask):
    """Adaptador que convierte un RunningProcess (subprocess) en DownloadTask."""

    def __init__(self, process: RunningProcess) -> None:
        self._process = process

    async def progress(self) -> AsyncIterator[DownloadProgress]:
        async for progress in self._process.progress():
            try:
                speed = float(progress.speed)
                downloaded = int(progress.downloaded)
                total = int(progress.total)
            except ValueError:
                speed = 0.0
                downloaded = 0
                total = 0
            yield DownloadProgress(
                downloaded_bytes=downloaded,
                total_bytes=total,
                speed_bps=speed,
            )

    async def cancel(self) -> None:
        await self._process.kill()

    async def pause(self) -> None:
        await self._process.terminate()

    async def finalize(self) -> DownloadResult:
        result = await self._process.wait()
        return DownloadResult(
            exit_code=result.exit_code or 0,
            stderr=result.stderr,
        )
