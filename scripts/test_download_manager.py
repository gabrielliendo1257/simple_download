import asyncio

from simple_downloader.app.manager import DownloadManager, DownloadRequest
from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.event import EventBus
from simple_downloader.executor import (
    ExecutableName,
    ExecutableSpec,
    ExecutorDetector,
    ExecutorRegistry,
)
from simple_downloader.process import AsyncProcessExecutor
from simple_downloader.sources import SourceProvider


async def test_manager() -> None:
    event_bus = EventBus()
    executor_registry = ExecutorRegistry()
    async_process_executor = AsyncProcessExecutor()
    detector = ExecutorDetector(executor=async_process_executor)

    executable = await detector.detect(
        executable_spec=ExecutableSpec(name=ExecutableName.YT_DLP.value)
    )
    executor_registry.register(executable=executable)

    source_provider = SourceProvider(
        executor_registry=executor_registry, process_executor=async_process_executor
    )
    download_scheduler = DownloadScheduler(
        event_bus=event_bus,
    )
    await download_scheduler.start()
    download_manager = DownloadManager(
        event_bus=event_bus,
        source_provider=source_provider,
        download_scheduler=download_scheduler,
    )

    ##################################
    #
    ##################################
    job = await download_manager.enqueue(
        request=DownloadRequest(url="https://youtu.be/_nXPqePbBac?si=UTcD98Ckeeb5UJBT", extract_audio=True)
    )
    job_one = await download_manager.enqueue(
        request=DownloadRequest(url="https://youtu.be/OM65IB-gy2w?si=ZlHsVYwqJZBIDDYk", extract_audio=True)
    )
    job_t = await download_manager.enqueue(
        request=DownloadRequest(url="https://youtu.be/PiSfroMnqWA?si=qOwGhp4n-QACGyq9", extract_audio=True)
    )

    asyncio.create_task(download_manager.start(job_id=job.id))
    asyncio.create_task(download_manager.start(job_id=job_one.id))
    asyncio.create_task(download_manager.start(job_id=job_t.id))

    # tasks = [
    #     asyncio.create_task(download_manager.start(job_id=jb.id))
    #     for jb in [job, job_one]
    # ]

    # asyncio.get_event_loop().call_later(
    #     delay=9,
    #     callback=lambda: asyncio.create_task(download_manager.pause(job_id=job.id)),
    # )
    # asyncio.get_event_loop().call_later(
    #     delay=18,
    #     callback=lambda: asyncio.create_task(download_manager.resume(job_id=job.id)),
    # )
    # await asyncio.gather(*tasks)

    await event.wait()
    await download_scheduler.down()

    # asyncio.create_task(download_manager.start(job_id=job.id))
    # await download_manager.start(job_id=job_one.id)


event = asyncio.Event()

try:
    asyncio.run(test_manager())
    print("Terminated")
except KeyboardInterrupt:
    print("Press CTRL-C")
    event.set()
    pass
