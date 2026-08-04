import asyncio

import pytest

from simple_downloader.domain.models import (
    DownloadOutput,
    DownloadProgress,
    DownloadRequest,
)
from simple_downloader.engines.http import HttpDownloadTask, HttpEngine


class FakeStreamClient:
    """Cliente que devuelve un stream de chunks y registra el referer."""

    def __init__(self, chunks: list[bytes], total: int | None = None) -> None:
        self.chunks = chunks
        self.total = total
        self.seen: list[tuple[str | None, str]] = []

    def with_referer(self, referer: str) -> "FakeStreamClient":
        client = FakeStreamClient(self.chunks, self.total)
        client.referer = referer
        return client

    async def stream(self, url: str):
        yield self.total, b""
        for chunk in self.chunks:
            yield None, chunk

    async def get(self, url: str) -> bytes:
        return b"".join(self.chunks)


def test_http_engine_supports_direct_file_urls() -> None:
    engine = HttpEngine(http=FakeStreamClient([]))

    assert engine.supports("https://x/3146165.720.mp4?s=1&ts=2")
    assert engine.supports("https://x/audio.mp3")
    assert engine.supports("https://x/archivo.pdf")
    assert not engine.supports("https://x/master.m3u8")
    assert not engine.supports("https://x/watch?v=abc")


def test_http_engine_wins_over_ytdlp_for_mp4() -> None:
    from simple_downloader.engines import EngineRegistry
    from simple_downloader.engines.ytdlp import YtDlpEngine

    registry = EngineRegistry()
    registry.register(HttpEngine(http=FakeStreamClient([])))
    registry.register(YtDlpEngine(source_provider=object()))  # type: ignore[arg-type]

    engine = registry.engine_for("https://h70v.eulue.com/x/3146165.720.mp4?s=1")
    assert engine.name == "http"


def test_http_task_downloads_all_chunks(tmp_path) -> None:
    client = FakeStreamClient([b"abc", b"def", b"ghi"], total=9)
    task = HttpDownloadTask(
        url="https://x/file.mp4", out_file=tmp_path / "out.mp4", http=client
    )

    async def drive() -> list[DownloadProgress]:
        progress: list[DownloadProgress] = []
        async for item in task.progress():
            progress.append(item)
        result = await task.finalize()
        return progress, result

    progress, result = asyncio.run(drive())

    assert (tmp_path / "out.mp4").read_bytes() == b"abcdefghi"
    assert result.exit_code == 0
    assert progress[-1].downloaded_bytes == 9
    assert progress[-1].total_bytes == 9


def test_http_task_reports_unknown_total(tmp_path) -> None:
    client = FakeStreamClient([b"a" * 100], total=None)
    task = HttpDownloadTask(
        url="https://x/file.mp4", out_file=tmp_path / "out.mp4", http=client
    )

    progress: list[DownloadProgress] = []

    async def drive() -> None:
        async for item in task.progress():
            progress.append(item)
        await task.finalize()

    asyncio.run(drive())

    assert progress[-1].downloaded_bytes == 100
    assert progress[-1].total_bytes is None


def test_http_task_cancel_stops_writing(tmp_path) -> None:
    slow = [b"x" * 1024 for _ in range(100)]

    class SlowClient(FakeStreamClient):
        async def stream(self, url: str):
            yield None, b""
            for chunk in slow:
                yield None, chunk
                await asyncio.sleep(0.01)

    task = HttpDownloadTask(
        url="https://x/file.mp4", out_file=tmp_path / "out.mp4", http=SlowClient(slow)
    )

    async def consume() -> None:
        async for _ in task.progress():
            pass

    async def drive() -> None:
        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

    asyncio.run(drive())

    written = (tmp_path / "out.mp4").stat().st_size
    assert written < sum(len(c) for c in slow)


def test_http_engine_create_task_uses_referer(tmp_path) -> None:
    client = FakeStreamClient([b"data"], total=4)

    async def drive() -> None:
        task = await HttpEngine(http=client).create_task(
            DownloadRequest(
                url="https://h70v.eulue.com/video/3146165.720.mp4?s=1",
                output=DownloadOutput(
                    directory=tmp_path, filename="out.mp4"
                ),
            )
        )
        await task.finalize()

    asyncio.run(drive())
    assert (tmp_path / "out.mp4").read_bytes() == b"data"


def test_http_task_raises_on_http_error(tmp_path) -> None:
    class ErrorClient:
        async def stream(self, url: str):
            yield None, b""
            raise RuntimeError("HTTP 403")

    task = HttpDownloadTask(
        url="https://x/file.mp4", out_file=tmp_path / "out.mp4", http=ErrorClient()
    )

    with pytest.raises(RuntimeError, match="403"):

        async def drive() -> None:
            async for _ in task.progress():
                pass

        asyncio.run(drive())
