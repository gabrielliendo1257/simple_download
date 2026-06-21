import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from simple_downloader.errors import ProcessError, SourceUnvaliableError
from simple_downloader.executor import (Executable, ExecutableName,
                                        ExecutableStatus, ExecutorRegistry)
from simple_downloader.process import (ProcessExecutor, ProcessRequest,
                                       RunningProcess)


@dataclass(frozen=True)
class VideoMetadata:
    id: str
    title: str
    uploader: str | None
    duration: int | None
    webpage_url: str


class SourceProvider:
    def __init__(
        self, executor_registry: ExecutorRegistry, process_executor: ProcessExecutor
    ) -> None:
        self.executor_registry = executor_registry
        self.process_executor = process_executor

    def get_source(self, executable_name: ExecutableName):
        executable: Executable = self.executor_registry.get_executor(
            executable_name=executable_name
        )

        if executable.status == ExecutableStatus.NOT_FOUND:
            raise SourceUnvaliableError(executable_name.value)

        if executable_name == ExecutableName.YT_DLP:
            return YtDlpSource(executable=executable, executor=self.process_executor)

        raise SourceUnvaliableError(executable_name.value)


class Source(Protocol):
    _executable: Executable
    _executor: ProcessExecutor

    async def metadata(self, url: str) -> VideoMetadata: ...

    async def formats(self, url: str) -> dict: ...

    async def download(self) -> RunningProcess: ...


class YtDlpSource(Source):
    def __init__(self, executable: Executable, executor: ProcessExecutor) -> None:
        super().__init__()
        self._executable = executable
        self._executor = executor

    async def metadata(self, url: str) -> VideoMetadata:
        request = ProcessRequest(
            executable=self._executable.path,
            args=["--dump-single-json", "--no-playlist", url],
        )

        result = await self._executor.execute(request=request)
        if result.exit_code != 0:
            raise ProcessError(result.stderr)

        data = json.loads(result.stdout)
        print("Data: ", data)

        return VideoMetadata(
            id=data["id"],
            title=data["title"],
            uploader=data["uploader"],
            webpage_url=data["webpage_url"],
            duration=data["duration"],
        )

    async def formats(self, url: str) -> dict:
        request = ProcessRequest(
            executable=self._executable.path,
            args=["--dump-single-json", "--no-playlist", url],
        )

        result = await self._executor.execute(request=request)
        if result.exit_code != 0:
            raise ProcessError(result.stderr)

        data = json.loads(result.stdout)
        print("Data: ", data)

        return data["formats"]

    async def download(
        self, url: str, output: Path | None = None, format_id: str | None = None
    ) -> RunningProcess:
        args = [
                "--newline"
                ]

        args.extend(["--progress-template", 'PROGRESS={"downloaded":"%(progress.downloaded_bytes)s","total":"%(progress.total_bytes)s","speed":"%(progress.speed)s"}'])

        if output is not None:
            args.extend(["-o", str(output)])

        if format_id is not None:
            args.extend(["-f", format_id])

        args.append(url)

        request = ProcessRequest(executable=self._executable.path, args=args)

        return await self._executor.start(request=request)

