import asyncio

import pytest

from simple_downloader.domain.models import DownloadProgress, DownloadResult
from simple_downloader.engines.hls.fetch import SegmentFetcher
from simple_downloader.engines.hls.models import Segment
from simple_downloader.engines.hls.task import HlsTask, SegmentDownloadError


class FakeSegmentFetcher:
    def __init__(self, payload: bytes, delay: float = 0.0) -> None:
        self._payload = payload
        self._delay = delay

    async def fetch(self, segment: Segment) -> bytes:
        await asyncio.sleep(self._delay)
        return self._payload


def _segments(count: int) -> list[Segment]:
    return [Segment(index=i, uri=f"https://s/seg{i}.ts") for i in range(count)]


@pytest.fixture
def tmp_out(tmp_path):
    return tmp_path / "out.ts"


async def test_hls_task_writes_all_segments_in_order(tmp_path) -> None:
    payload = b"a" * 188
    out = tmp_path / "out.ts"
    task = HlsTask(
        out_file=out,
        fetcher=FakeSegmentFetcher(payload),
        segments=_segments(4),
    )

    await task.progress().__aiter__().__anext__()
    result = await task.finalize()

    assert result.exit_code == 0
    assert out.read_bytes() == payload * 4


async def test_hls_task_writes_init_map_before_segments(tmp_path) -> None:
    init = b"\x00\x00\x00\x18ftypisom"  # ftyp + moov de ejemplo
    seg = b"moof\x00\x00\x00\x10mfhd"  # fragmentos m4s

    class Fmp4Fetcher:
        async def fetch_init(self, uri: str) -> bytes:
            assert uri == "https://s/init.mp4"
            return init

        async def fetch(self, segment: Segment) -> bytes:
            return seg

    out = tmp_path / "out.mp4"
    task = HlsTask(
        out_file=out,
        fetcher=Fmp4Fetcher(),
        segments=_segments(3),
        init_uri="https://s/init.mp4",
    )

    await task.progress().__aiter__().__anext__()
    result = await task.finalize()

    assert result.exit_code == 0
    assert out.read_bytes() == init + seg * 3


async def test_hls_task_writes_out_of_order_chunks_ordered(tmp_path) -> None:
    async def slow_first(segment: Segment) -> bytes:
        if segment.index == 0:
            await asyncio.sleep(0.05)
        return segment.index.to_bytes(1, "big")

    class ReorderingFetcher:
        async def fetch(self, segment: Segment) -> bytes:
            return await slow_first(segment)

    out = tmp_path / "out.ts"
    task = HlsTask(
        out_file=out,
        fetcher=ReorderingFetcher(),
        segments=_segments(4),
    )

    await asyncio.gather(task.progress().__aiter__().__anext__(), task.finalize())

    assert out.read_bytes() == bytes([0, 1, 2, 3])


async def test_hls_task_progress_is_monotonic(tmp_path) -> None:
    out = tmp_path / "out.ts"
    task = HlsTask(
        out_file=out,
        fetcher=FakeSegmentFetcher(b"x" * 10, delay=0.01),
        segments=_segments(6),
        max_parallel=2,
    )

    seen: list[int] = []
    async for progress in task.progress():
        seen.append(progress.downloaded_bytes)

    assert seen == sorted(seen)


async def test_hls_task_segment_failure_propagates(tmp_path) -> None:
    class FailingFetcher:
        async def fetch(self, segment: Segment) -> bytes:
            raise RuntimeError("network")

    out = tmp_path / "out.ts"
    task = HlsTask(
        out_file=out,
        fetcher=FailingFetcher(),
        segments=_segments(2),
    )

    with pytest.raises(SegmentDownloadError):
        await task.progress().__aiter__().__anext__()
        await task.finalize()


async def test_hls_task_cancel_stops_workers_and_discards(tmp_path) -> None:
    out = tmp_path / "out.ts"
    task = HlsTask(
        out_file=out,
        fetcher=FakeSegmentFetcher(b"x" * 2, delay=0.02),
        segments=_segments(10),
    )

    async def drive():
        await task.progress().__aiter__().__anext__()
        await task.finalize()

    run = asyncio.create_task(drive())
    await asyncio.sleep(0.05)
    await task.cancel()
    await asyncio.sleep(0.05)

    assert not out.exists()  # cancelar abandona: sin basura en disco


async def test_hls_task_pause_cancels_cleanly(tmp_path) -> None:
    out = tmp_path / "out.ts"
    task = HlsTask(
        out_file=out,
        fetcher=FakeSegmentFetcher(b"y" * 2, delay=0.02),
        segments=_segments(10),
    )

    async def drive():
        await task.progress().__aiter__().__anext__()
        await task.finalize()

    run = asyncio.create_task(drive())
    await asyncio.sleep(0.02)
    await task.pause()
    # Task arrancado y pausado: cancellado de forma limpia
    with pytest.raises(asyncio.CancelledError):
        await run
