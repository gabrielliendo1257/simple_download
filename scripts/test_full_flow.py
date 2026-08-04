import asyncio
from pathlib import Path

from simple_downloader.app.manager import DownloadManager
from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.domain.event import DownloadStateChangedEvent
from simple_downloader.domain.models import (
    DownloadOutput,
    DownloadRequest,
    DownloadState,
)
from simple_downloader.domain.protocols import HttpClient
from simple_downloader.engines import EngineRegistry
from simple_downloader.engines.hls import HlsEngine, Segment
from simple_downloader.engines.hls.fetch import SegmentFetcher
from simple_downloader.engines.hls.task import HlsTask
from simple_downloader.event import EventBus


class SlowHttp(HttpClient):
    def __init__(self) -> None:
        self._png = (
            b"\x89PNG\r\n\x1a\n"
            + b"junk"
            + b"IEND\xaeB`\x82"
            + b"\x00" * 5
            + bytes([0x47]) * 188 * 2
        )

    async def get(self, url: str) -> bytes:
        if url.endswith(".m3u8"):
            return b"#EXTM3U\n#EXT-X-ENDLIST\n"
        await asyncio.sleep(0.5)
        return self._png


class SlowHlsEngine(HlsEngine):
    def __init__(self, http: HttpClient, tmp: Path) -> None:
        super().__init__(http)
        self._tmp = tmp

    def supports(self, url: str) -> bool:
        return True

    async def create_task(self, request):
        fetcher = SegmentFetcher(self._http)
        segments = [Segment(index=i, uri=f"https://s/seg{i}.ts") for i in range(6)]
        return HlsTask(
            out_file=self._tmp / f"{request.url.split('/')[-1]}.ts",
            fetcher=fetcher,
            segments=segments,
        )


async def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        bus = EventBus()
        transitions: list[tuple[str, str]] = []

        def on_state(e):
            transitions.append((str(e.job_id)[:4], e.state.name))

        bus.subscribe(DownloadStateChangedEvent, on_state)

        sched = DownloadScheduler(bus, max_workers=2)
        sched.start()

        reg = EngineRegistry()
        reg.register(SlowHlsEngine(SlowHttp(), tmp))

        mgr = DownloadManager(bus, reg, sched)

        j1 = await mgr.enqueue(
            DownloadRequest(
                url="a://job1",
                output=DownloadOutput(directory=tmp, filename="j1.ts"),
            )
        )
        j2 = await mgr.enqueue(
            DownloadRequest(
                url="a://job2",
                output=DownloadOutput(directory=tmp, filename="j2.ts"),
            )
        )

        await mgr.start(j1.id)
        await asyncio.sleep(0.05)
        await mgr.start(j2.id)
        await asyncio.sleep(0.15)

        await mgr.pause(j1.id)
        await asyncio.sleep(0.1)
        assert j1.state is DownloadState.PAUSED, j1.state
        print("pause OK, j1 estado:", j1.state.name)

        await mgr.resume(j1.id)
        await asyncio.sleep(0.1)
        assert j1.state is DownloadState.RUNNING, j1.state

        await asyncio.sleep(4.0)
        await sched.finish()

        assert j1.state is DownloadState.COMPLETED, j1.state
        assert j2.state is DownloadState.COMPLETED, j2.state
        assert (tmp / "job1.ts").exists()
        assert (tmp / "job2.ts").exists()

        states_j1 = [s for j, s in transitions if j == str(j1.id)[:4]]
        states_j2 = [s for j, s in transitions if j == str(j2.id)[:4]]
        print("j1:", states_j1)
        print("j2:", states_j2)
        assert states_j1 == ["QUEUED", "RUNNING", "PAUSED", "RUNNING", "COMPLETED"], states_j1
        assert states_j2 == ["QUEUED", "RUNNING", "COMPLETED"], states_j2
        print("SMOKE FULL OK")


asyncio.run(main())