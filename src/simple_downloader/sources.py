from ast import arg
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, override

from simple_downloader.errors import ProcessError, SourceUnvaliableError
from simple_downloader.executor import (
    Executable,
    ExecutableName,
    ExecutableStatus,
    ExecutorRegistry,
)
from simple_downloader.process import ProcessExecutor, ProcessRequest, RunningProcess


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
        print("Executable: ", executable)

        if (
            executable.status == ExecutableStatus.NOT_FOUND
            or executable.status == ExecutableStatus.ERROR
        ):
            raise SourceUnvaliableError(executable_name.value)

        if executable_name == ExecutableName.YT_DLP:
            return YtDlpSource(executable=executable, executor=self.process_executor)

        raise SourceUnvaliableError(executable_name.value)


class Source(Protocol):
    _executable: Executable
    _executor: ProcessExecutor

    async def metadata(self, url: str) -> VideoMetadata: ...

    async def formats(self, url: str) -> dict: ...

    async def download(
        self,
        url: str,
        extract_audio: bool = False,
        output: Path | None = None,
        format_id: str | None = None,
        resume: bool = False,
    ) -> RunningProcess: ...


class YtDlpSource(Source):
    def __init__(self, executable: Executable, executor: ProcessExecutor) -> None:
        super().__init__()
        self._executable = executable
        self._executor = executor

    async def metadata(self, url: str) -> VideoMetadata:
        args = ["--dump-single-json", "--no-playlist"]
        args.append(url)

        request = ProcessRequest(executable=self._executable.path, args=args)

        result = await self._executor.execute(request=request)
        if result.exit_code != 0:
            raise ProcessError(result.stderr)

        data = json.loads(result.stdout)

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

        return data["formats"]

    @override
    async def download(
        self,
        url: str,
        extract_audio: bool = False,
        output: Path | None = None,
        format_id: str | None = None,
        resume: bool = False,
    ) -> RunningProcess:
        args = ["--newline"]
        args.extend(
            [
                "--progress-template",
                # "PROGRESS=%(progress)j",
                'PROGRESS={"downloaded":"%(progress.downloaded_bytes)s","total":"%(progress.total_bytes)s","speed":"%(progress.speed)s"}',
            ]
        )

        if resume:
            args.append("-c")
        else:
            if extract_audio:
                args.extend(["-x", "--audio-format", "best", "--no-keep-video"])
            elif format_id is not None:
                args.extend(["-f", format_id])
            else:
                # args.extend(["-f", "best[height==720]"])
                args.extend(
                    ["-f", "bestvideo[height<=720]+bestaudio/best[height<=720]"]
                )

        if output is not None:
            args.extend(["-o", str(output)])

        args.append(url)
        print("args: ", args)
        assert self._executable.path is not None, "Path of executable is None"

        request: ProcessRequest = ProcessRequest(
            executable=self._executable.path, args=args
        )

        return await self._executor.start(request=request)
