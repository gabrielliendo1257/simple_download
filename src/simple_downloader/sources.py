from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, override

from simple_downloader.domain.models import DownloadOutput
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


@dataclass(frozen=True)
class Format:
    format_id: str
    ext: str
    resolution: str
    filesize_approx: int | None = None


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

    async def metadata(
        self, url: str, cookies_from_browser: str | None = None
    ) -> VideoMetadata: ...

    async def formats(self, url: str) -> dict: ...

    async def download(
        self,
        url: str,
        extract_audio: bool = False,
        output: Path | str | None = None,
        format_id: str | None = None,
        resume: bool = False,
        headers: dict[str, str] | None = None,
        cookies_path: str | None = None,
        cookies_from_browser: str | None = None,
    ) -> RunningProcess: ...


class YtDlpSource(Source):
    def __init__(self, executable: Executable, executor: ProcessExecutor) -> None:
        super().__init__()
        self._executable = executable
        self._executor = executor

    async def metadata(
        self, url: str, cookies_from_browser: str | None = None
    ) -> VideoMetadata:
        args = ["--dump-single-json", "--no-playlist"]
        if cookies_from_browser:
            args.extend(["--cookies-from-browser", cookies_from_browser])
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

    async def formats(self, url: str) -> list[Format]:
        request = ProcessRequest(
            executable=self._executable.path,
            args=["--dump-single-json", "--no-playlist", url],
        )

        result = await self._executor.execute(request=request)
        if result.exit_code != 0:
            raise ProcessError(result.stderr)

        data = json.loads(result.stdout)

        formats: list[Format] = []
        formats_from_source: list[dict] = data["formats"]
        for fmt in formats_from_source:
            formats.append(
                Format(
                    fmt["format_id"],
                    fmt["ext"],
                    fmt["resolution"],
                    fmt.get("filesize_approx"),
                )
            )
        return formats

    @override
    async def download(
        self,
        url: str,
        extract_audio: bool = False,
        output: Path | str | None = None,
        format_id: str | None = None,
        resume: bool = False,
        headers: dict[str, str] | None = None,
        cookies_path: str | None = None,
        cookies_from_browser: str | None = None,
    ) -> RunningProcess:
        args = ["--newline"]
        args.extend(
            [
                "--progress-template",
                # "PROGRESS=%(progress)j",
                'PROGRESS={"downloaded":"%(progress.downloaded_bytes)s","total":"%(progress.total_bytes)s","speed":"%(progress.speed)s"}',
            ]
        )

        if headers:
            for key, value in headers.items():
                args.extend(["--add-header", f"{key}: {value}"])

        if cookies_path:
            args.extend(["--cookies", cookies_path])
        if cookies_from_browser:
            args.extend(["--cookies-from-browser", cookies_from_browser])

        if resume:
            args.append("-c")
        else:
            if extract_audio:
                args.extend(["-x", "--audio-format", "best", "--no-keep-video"])
            elif format_id is not None:
                args.extend(["-f", format_id])
            else:
                # fallback hasta "best" para URLs directas (extractor generic)
                args.extend(
                    [
                        "-f",
                        "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                    ]
                )

        if output is not None:
            args.extend(["-o", _output_arg(output)])

        args.append(url)
        assert self._executable.path is not None, "Path of executable is None"

        request: ProcessRequest = ProcessRequest(
            executable=self._executable.path, args=args
        )

        return await self._executor.start(request=request)


def _output_arg(output: Path | str | DownloadOutput | None) -> str | None:
    """Convierte la salida en el `-o` de yt-dlp.

    `DownloadOutput` con template usa placeholders tipo `{title}` que se
    traducen a la sintaxis de yt-dlp (`%(title)s`).
    """
    if output is None:
        return None
    if not isinstance(output, DownloadOutput):
        return str(output)

    if output.create_directories:
        output.directory.mkdir(parents=True, exist_ok=True)

    if output.filename:
        return str(output.directory / output.filename)
    if output.template:
        return str(output.directory / _translate_template(output.template))
    return str(output.directory)


def _translate_template(template: str) -> str:
    placeholders = {"title", "ext", "id", "date", "resolution"}
    translated = template
    for key in placeholders:
        translated = translated.replace("{" + key + "}", "%(" + key + ")s")
    return translated
