import asyncio
from pathlib import Path

from simple_downloader.domain.models import (
    DownloadOutput,
    DownloadRequest,
    DownloadState,
)
from simple_downloader.domain.protocols import HttpClient
from simple_downloader.engines.hls import HlsEngine, HlsPlaylist, Segment
from simple_downloader.engines.hls.fetch import SegmentFetcher, unwrap_ts
from simple_downloader.engines.hls.task import HlsTask


class FakeHttp(HttpClient):
    def __init__(self, segments: int) -> None:
        self.hits = 0
        self._png = (
            b"\x89PNG\r\n\x1a\n"
            + b"junk"
            + b"IEND\xaeB`\x82"
            + b"\x00" * 5
            + bytes([0x47]) * 188 * 4
        )
        self._segments = segments

    async def get(self, url: str) -> bytes:
        self.hits += 1
        if url.endswith(".m3u8"):
            return b"#EXTM3U\n#EXT-X-ENDLIST\n"
        return self._png


def build_segments(n: int) -> list[Segment]:
    return [Segment(index=i, uri=f"https://s/seg{i}.ts") for i in range(n)]


async def test_hls_task(tmp: Path) -> None:
    http = FakeHttp(segments=4)
    fetcher = SegmentFetcher(http)
    task = HlsTask(out_file=tmp / "out.ts", fetcher=fetcher, segments=build_segments(4))

    written: list[int] = []
    async for p in task.progress():
        written.append(p.downloaded_bytes)

    result = await task.finalize()
    assert result.exit_code == 0, result
    data = (tmp / "out.ts").read_bytes()
    assert data == bytes([0x47]) * 188 * 4 * 4, len(data)
    assert len(written) >= 1, written
    assert written == sorted(written), written
    print("HlsTask OK, bytes escritos:", len(data), "| progreso:", written)


async def test_pause(tmp: Path) -> None:
    http = FakeHttp(segments=4)

    class SlowSegmentFetcher(SegmentFetcher):
        async def fetch(self, segment: Segment) -> bytes:
            await asyncio.sleep(0.05)
            return await super().fetch(segment)

    task = HlsTask(
        out_file=tmp / "out2.ts",
        fetcher=SlowSegmentFetcher(http),
        segments=build_segments(10),
    )

    run = asyncio.create_task(task.finalize())
    await asyncio.sleep(0.3)
    await task.pause()
    try:
        await run
    except asyncio.CancelledError:
        pass
    assert (tmp / "out2.ts").exists()
    print("HlsTask pause OK")


async def test_cancel(tmp: Path) -> None:
    http = FakeHttp(segments=4)

    class SlowSegmentFetcher2(SegmentFetcher):
        async def fetch(self, segment: Segment) -> bytes:
            await asyncio.sleep(0.05)
            return await super().fetch(segment)

    task = HlsTask(
        out_file=tmp / "out3.ts",
        fetcher=SlowSegmentFetcher2(http),
        segments=build_segments(10),
    )
    run = asyncio.create_task(task.finalize())
    await asyncio.sleep(0.15)
    await task.cancel()
    try:
        await run
    except asyncio.CancelledError:
        pass
    print("HlsTask cancel OK")


async def test_engine(tmp: Path) -> None:
    http = FakeHttp(segments=4)
    engine = HlsEngine(http)

    master = (
        b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100,RESOLUTION=640x360\nlow.m3u8\n"
        b"#EXT-X-STREAM-INF:BANDWIDTH=5000,RESOLUTION=1080x1920\nhigh.m3u8\n"
    )

    class MasterHttp(HttpClient):
        async def get(self, url: str) -> bytes:
            if url == "https://m/master.m3u8":
                return master
            return b"#EXTM3U\n#EXT-X-ENDLIST\n"

    engine = HlsEngine(MasterHttp())
    task = await engine.create_task(
        DownloadRequest(
            url="https://m/master.m3u8",
            output=DownloadOutput(directory=tmp, filename="video.ts"),
        )
    )
    assert hasattr(task, "segments")
    await asyncio.sleep(0.1)
    print("HlsEngine OK")


async def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        await test_hls_task(tmp)
        await test_pause(tmp)
        await test_cancel(tmp)
        await test_engine(tmp)


if __name__ == "__main__":
    asyncio.run(main())