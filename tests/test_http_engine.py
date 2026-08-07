import asyncio

import pytest

from simple_downloader.domain.models import (
    DownloadOutput,
    DownloadProgress,
    DownloadRequest,
)
from simple_downloader.engines.common import resume_plan, save_resume_meta
from simple_downloader.engines.http import HttpDownloadTask, HttpEngine
from simple_downloader.infra.http import describe_http_error


def test_describe_http_error_translates_timeout() -> None:
    assert describe_http_error(TimeoutError()) == (
        "timeout: el servidor no respondió a tiempo"
    )
    assert describe_http_error(asyncio.TimeoutError()) == (
        "timeout: el servidor no respondió a tiempo"
    )


async def test_http_engine_validate_passes_when_server_responds() -> None:
    engine = HttpEngine(http=FakeStreamClient(b"data", total=42))

    assert await engine.validate("https://x/file.mp4") is None


async def test_http_engine_validate_propagates_unreachable() -> None:
    class UnreachableClient:
        async def check(self, url: str) -> int | None:
            raise ConnectionError("sin conexión")

    engine = HttpEngine(http=UnreachableClient())

    with pytest.raises(ConnectionError):
        await engine.validate("https://x/file.mp4")


async def test_http_engine_validate_noop_without_check() -> None:
    class MinimalClient:
        async def get(self, url: str) -> bytes:
            raise AssertionError("validate no debe descargar el body")

    engine = HttpEngine(http=MinimalClient())

    assert await engine.validate("https://x/file.mp4") is None


class FakeStreamClient:
    """Cliente que sirve `data` con soporte de Range simulado.

    - offset=0 -> 200 con todo el contenido.
    - offset>0  -> `status_for_range`: 206 (reanuda desde offset),
      200 (ignora el Range y manda todo) o 416 (ya completo).
    """

    def __init__(
        self,
        data: bytes,
        total: int | None = None,
        status_for_range: int = 206,
    ) -> None:
        self.data = data
        self.total = total  # None = servidor sin Content-Length
        self.status_for_range = status_for_range
        self.last_offset: int | None = None

    def with_referer(self, referer: str) -> "FakeStreamClient":
        client = FakeStreamClient(self.data, self.total, self.status_for_range)
        client.referer = referer
        return client

    async def check(self, url: str) -> int | None:
        return self.total

    async def stream(self, url: str, *, offset: int = 0, headers: dict | None = None):
        self.last_offset = offset
        if offset > 0:
            if self.status_for_range == 416:
                yield 416, self.total, b""
                return
            yield self.status_for_range, self.total, b""
            data = self.data[offset:] if self.status_for_range == 206 else self.data
        else:
            yield 200, self.total, b""
            data = self.data
        for start in range(0, len(data), 64):
            yield None, None, data[start : start + 64]

    async def get(self, url: str) -> bytes:
        return self.data


def test_http_engine_supports_direct_file_urls() -> None:
    engine = HttpEngine(http=FakeStreamClient(b""))

    assert engine.supports("https://x/3146165.720.mp4?s=1&ts=2")
    assert engine.supports("https://x/audio.mp3")
    assert engine.supports("https://x/archivo.pdf")
    assert not engine.supports("https://x/master.m3u8")
    assert not engine.supports("https://x/watch?v=abc")


def test_http_engine_supports_media_in_query_param() -> None:
    engine = HttpEngine(http=FakeStreamClient(b""))
    url = (
        "https://cdn.leak-sex-tape.com/remote_control.php"
        "?file=ZYmQu5thjnltY3CQy9qGjA8xedD_NkoJAIS9rviIniovWQTyL7cwRLRWMYwbTgJ"
        "XxA6sCNepxDGvbYjMdQd01lewTFx_d78oBbdT8s2gJRmkInozz-MUcW1p0hncPRwal"
        "Qu1KIvv6gRspP0IMVSkGlf6fN82cOBx3KoFFztAZ4XlxpLBPIk8j7B-p4HXktJkq0"
        "vptAVJgMuR.mp4&acctoken=YzhjYWRlMTE4MWQ0ZjA1ZTY4ZmExNDZmMWJkNmE3ZTQ1"
    )

    assert engine.supports(url)
    assert not engine.supports("https://x/remote_control.php?file=report")
    assert not engine.supports("https://x/remote_control.php?file=page.html")
    assert not engine.supports("https://x/remote_control.php?token=abc.mp4")


def test_http_engine_wins_over_ytdlp_for_mp4() -> None:
    from simple_downloader.engines import EngineRegistry
    from simple_downloader.engines.ytdlp import YtDlpEngine

    registry = EngineRegistry()
    registry.register(HttpEngine(http=FakeStreamClient(b"")))
    registry.register(YtDlpEngine(source_provider=object()))  # type: ignore[arg-type]

    engine = registry.engine_for("https://h70v.eulue.com/x/3146165.720.mp4?s=1")
    assert engine.name == "http"


def test_http_engine_wins_over_ytdlp_for_query_wrapper() -> None:
    from simple_downloader.engines import EngineRegistry
    from simple_downloader.engines.ytdlp import YtDlpEngine

    registry = EngineRegistry()
    registry.register(HttpEngine(http=FakeStreamClient(b"")))
    registry.register(YtDlpEngine(source_provider=object()))  # type: ignore[arg-type]

    engine = registry.engine_for(
        "https://cdn.leak-sex-tape.com/remote_control.php?file=video.mp4&token=x"
    )
    assert engine.name == "http"


def test_http_engine_named_from_query_media(tmp_path) -> None:
    async def drive() -> None:
        task = await HttpEngine(http=FakeStreamClient(b"data")).create_task(
            DownloadRequest(
                url="https://cdn.leak-sex-tape.com/remote_control.php"
                "?file=c8cade1181d4f05e68fa146f1bd6a7.mp4&acctoken=xyz",
                output=DownloadOutput(directory=tmp_path),
            )
        )
        await task.finalize()
        return task

    task = asyncio.run(drive())
    assert (tmp_path / "c8cade1181d4f05e68fa146f1bd6a7.mp4").read_bytes() == b"data"


def test_http_task_downloads_all_chunks(tmp_path) -> None:
    client = FakeStreamClient(b"abcdefghi", total=9)
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
    client = FakeStreamClient(b"a" * 100, total=None)
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


def test_http_task_cancel_discards_partial(tmp_path) -> None:
    slow = [b"x" * 1024 for _ in range(100)]

    class SlowClient(FakeStreamClient):
        def __init__(self) -> None:
            super().__init__(b"".join(slow))

        async def stream(
            self, url: str, *, offset: int = 0, headers: dict | None = None
        ):
            yield 200, len(self.data), b""
            for chunk in slow:
                yield None, None, chunk
                await asyncio.sleep(0.01)

    task = HttpDownloadTask(
        url="https://x/file.mp4", out_file=tmp_path / "out.mp4", http=SlowClient()
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

    assert not (tmp_path / "out.mp4").exists()  # cancelar abandona: sin basura
    assert not (tmp_path / "out.mp4.resume.json").exists()


def test_http_task_pause_keeps_partial(tmp_path) -> None:
    slow = [b"x" * 1024 for _ in range(100)]

    class SlowClient(FakeStreamClient):
        def __init__(self) -> None:
            super().__init__(b"".join(slow))

        async def stream(
            self, url: str, *, offset: int = 0, headers: dict | None = None
        ):
            yield 200, len(self.data), b""
            for chunk in slow:
                yield None, None, chunk
                await asyncio.sleep(0.01)

    task = HttpDownloadTask(
        url="https://x/file.mp4", out_file=tmp_path / "out.mp4", http=SlowClient()
    )

    async def consume() -> None:
        async for _ in task.progress():
            pass

    async def drive() -> None:
        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        await task.pause()
        with pytest.raises(asyncio.CancelledError):
            await consumer

    asyncio.run(drive())

    written = (tmp_path / "out.mp4").stat().st_size
    assert written < sum(len(c) for c in slow)  # pausar conserva el parcial
    assert (tmp_path / "out.mp4.resume.json").exists()


def test_http_engine_create_task_uses_referer(tmp_path) -> None:
    client = FakeStreamClient(b"data")

    async def drive() -> None:
        task = await HttpEngine(http=client).create_task(
            DownloadRequest(
                url="https://h70v.eulue.com/video/3146165.720.mp4?s=1",
                output=DownloadOutput(directory=tmp_path, filename="out.mp4"),
            )
        )
        await task.finalize()

    asyncio.run(drive())
    assert (tmp_path / "out.mp4").read_bytes() == b"data"


def test_http_task_raises_on_http_error(tmp_path) -> None:
    class ErrorClient:
        async def stream(
            self, url: str, *, offset: int = 0, headers: dict | None = None
        ):
            yield None, None, b""
            raise RuntimeError("HTTP 403")

    task = HttpDownloadTask(
        url="https://x/file.mp4", out_file=tmp_path / "out.mp4", http=ErrorClient()
    )

    with pytest.raises(RuntimeError, match="403"):

        async def drive() -> None:
            async for _ in task.progress():
                pass

        asyncio.run(drive())


# ── reanudación ────────────────────────────────────────────────────────────


def test_resume_plan_fresh_and_complete(tmp_path) -> None:
    out = tmp_path / "f.mp4"
    assert resume_plan(out, url="https://x/1").offset == 0

    out.write_bytes(b"abc")
    plan = resume_plan(out, url="https://x/1", expected_total=3)
    assert plan.valid
    assert plan.offset == 3  # ya completo


def test_resume_plan_rejects_foreign_or_corrupt_partial(tmp_path) -> None:
    out = tmp_path / "f.mp4"
    out.write_bytes(b"abc")
    save_resume_meta(out, url="https://x/1", total_bytes=None)

    plan = resume_plan(out, url="https://x/2")
    assert not plan.valid
    assert "otra descarga" in (plan.reason or "")

    other = tmp_path / "g.mp4"
    other.write_bytes(b"abcdef")
    plan = resume_plan(other, expected_total=3)
    assert not plan.valid
    assert "supera" in (plan.reason or "")


def test_http_task_resumes_from_partial(tmp_path) -> None:
    data = b"a" * 500
    out = tmp_path / "out.mp4"
    out.write_bytes(data[:200])  # parcial previo (pausado)

    client = FakeStreamClient(data, total=500)
    task = HttpDownloadTask(url="https://x/file.mp4", out_file=out, http=client)

    progress: list[DownloadProgress] = []

    async def drive() -> None:
        async for item in task.progress():
            progress.append(item)
        await task.finalize()

    asyncio.run(drive())

    assert out.read_bytes() == data
    assert client.last_offset == 200  # pidió el rango
    assert task.resume_fallback is False
    assert progress[-1].downloaded_bytes == 500
    assert progress[-1].total_bytes == 500


def test_http_task_fallback_when_server_ignores_range(tmp_path) -> None:
    data = b"a" * 500
    out = tmp_path / "out.mp4"
    out.write_bytes(b"a" * 200)

    client = FakeStreamClient(data, total=500, status_for_range=200)
    task = HttpDownloadTask(url="https://x/file.mp4", out_file=out, http=client)

    progress: list[DownloadProgress] = []

    async def drive() -> None:
        async for item in task.progress():
            progress.append(item)
        await task.finalize()

    asyncio.run(drive())

    assert out.read_bytes() == data  # reinició desde 0 y descargó todo
    assert client.last_offset == 200  # intentó el rango
    assert task.resume_fallback is True
    assert "HTTP 200" in (task.resume_fallback_reason or "")
    assert progress[-1].downloaded_bytes == 500


def test_http_task_marks_complete_on_416(tmp_path) -> None:
    data = b"a" * 500
    out = tmp_path / "out.mp4"
    out.write_bytes(data)  # ya completo en disco

    client = FakeStreamClient(data, status_for_range=416)
    task = HttpDownloadTask(url="https://x/file.mp4", out_file=out, http=client)

    async def drive() -> None:
        async for _ in task.progress():
            pass
        await task.finalize()

    asyncio.run(drive())

    assert out.read_bytes() == data
    assert (tmp_path / "out.mp4.resume.json").exists()  # metadatos del parcial
