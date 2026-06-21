import asyncio
import json

from simple_downloader.executor import (
    ExecutableName,
    ExecutableSpec,
    ExecutorDetector,
    ExecutorRegistry,
)
from simple_downloader.process import AsyncProcessExecutor, DownloadProgress
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
    metadata = await source.metadata("https://youtu.be/VwNPDISsjbU?si=X7fqRX5pd_E6VzAv")
    formats = await source.formats(
        url="https://youtu.be/_nXPqePbBac?si=ePJVOTX1Selq-E2D"
    )
    runner = await source.download(
        url="https://youtu.be/_nXPqePbBac?si=ePJVOTX1Selq-E2D"
    )
    print("Metadata: ", metadata)
    print("Formats: ", formats)
    async for line in runner.stdout_lines():
        if "[download]" in line:
            print(line)
        elif line.startswith("PROGRESS="):
            try:
                progress = DownloadProgress(**json.loads(line[9:]))
                print(progress)
            except json.JSONDecodeError:
                continue


asyncio.run(test_executor())
