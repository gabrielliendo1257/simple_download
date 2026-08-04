import asyncio

from simple_downloader.app.manager import DownloadManager
from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.domain.models import DownloadRequest
from simple_downloader.engines import EngineRegistry
from simple_downloader.engines.hls import HlsEngine
from simple_downloader.engines.ytdlp import YtDlpEngine
from simple_downloader.event import EventBus
from simple_downloader.executor import (
    ExecutableName,
    ExecutableSpec,
    ExecutorDetector,
    ExecutorRegistry,
)
from simple_downloader.infra.http import AioHttpClient
from simple_downloader.process import AsyncProcessExecutor
from simple_downloader.sources import SourceProvider


async def test_manager() -> None:
    event_bus = EventBus()
    async_process_executor = AsyncProcessExecutor()
    detector = ExecutorDetector(executor=async_process_executor)

    executable = await detector.detect(
        executable_spec=ExecutableSpec(name=ExecutableName.YT_DLP.value)
    )
    executor_registry = ExecutorRegistry()
    executor_registry.register(executable=executable)

    source_provider = SourceProvider(
        executor_registry=executor_registry,
        process_executor=async_process_executor,
    )

    engine_registry = EngineRegistry()
    engine_registry.register(HlsEngine(http=AioHttpClient()))
    engine_registry.register(YtDlpEngine(source_provider=source_provider))

    download_scheduler = DownloadScheduler(event_bus=event_bus)
    download_scheduler.start()
    download_manager = DownloadManager(
        event_bus=event_bus,
        engine_registry=engine_registry,
        download_scheduler=download_scheduler,
    )

    job = await download_manager.enqueue(
        request=DownloadRequest(
            url="https://youtu.be/_nXPqePbBac?si=UTcD98Ckeeb5UJBT",
            extract_audio=True,
        )
    )
    job_one = await download_manager.enqueue(
        request=DownloadRequest(
            url="https://youtu.be/OM65IB-gy2w?si=ZlHsVYwqJZBIDDYk",
            extract_audio=True,
        )
    )
    job_t = await download_manager.enqueue(
        request=DownloadRequest(
            url="https://youtu.be/PiSfroMnqWA?si=qOwGhp4n-QACGyq9",
            extract_audio=True,
        )
    )

    await download_manager.start(job_id=job.id)
    await download_manager.start(job_id=job_one.id)
    await download_manager.start(job_id=job_t.id)

    await event.wait()
    await download_scheduler.finish()


event = asyncio.Event()

try:
    asyncio.run(test_manager())
    print("Terminated")
except KeyboardInterrupt:
    print("Press CTRL-C")
    event.set()