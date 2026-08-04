import asyncio

from simple_downloader.executor import (
    ExecutableName,
    ExecutableSpec,
    ExecutorDetector,
    ExecutorRegistry,
)
from simple_downloader.process import AsyncProcessExecutor
from simple_downloader.sources import SourceProvider


async def test_executor():
    async_executor = AsyncProcessExecutor()
    executor_register = ExecutorRegistry()
    detector = ExecutorDetector(executor=async_executor)
    source_provider = SourceProvider(
        executor_registry=executor_register, process_executor=async_executor
    )

    executables_spec = [
        ExecutableSpec(name=ExecutableName.YT_DLP.value),
        ExecutableSpec(name=ExecutableName.GALLERY_DL.value),
        ExecutableSpec(name="castellano"),
    ]

    for exec_spec in executables_spec:
        result_spec = await detector.detect(exec_spec)
        executor_register.register(executable=result_spec)

    source = source_provider.get_source(executable_name=ExecutableName.YT_DLP)
    # metadata = await source.metadata("https://youtu.be/VwNPDISsjbU?si=X7fqRX5pd_E6VzAv")
    formats = await source.formats(
        url="https://youtu.be/wr8WS1JyqQs?si=xe1fAYmGZJde7azv"
    )
    # runner = await source.download(
    #     url="https://youtu.be/leOpsVpDjao?si=5aBYlD8B0wBu0Hjk", extract_audio=True
    # )
    # print("Metadata: ", metadata)
    for fmt in formats:
        print(fmt)
        # elif fmt.get("vcodec") != 'none':
        #     print("Video: ", end="")
        #     print(fmt["format_id"], fmt.get("ext"), fmt.get("filesize_approx"), fmt["acodec"], fmt["vcodec"])
    # async for line in runner.progress():
    #     print("Line: ", line)


asyncio.run(test_executor())
