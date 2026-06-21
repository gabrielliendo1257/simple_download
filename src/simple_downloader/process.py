from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol


@dataclass(frozen=True)
class ProcessRequest:
    executable: Path
    args: list[str] = field(default_factory=list)
    cwd: Path | None = None
    env: Mapping[str, str] | None = None
    stdin: bytes | None = None
    timeout: float = 5.0


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    stdout: str | None
    stderr: str | None


@dataclass(frozen=True)
class DownloadProgress:
    downloaded: int
    total: int | None
    speed: int | None


@dataclass
class RunningProcess:
    process: asyncio.subprocess.Process
    request: ProcessRequest

    async def wait(self) -> ProcessResult:
        stdout, stderr = await asyncio.wait_for(
            self.process.communicate(self.request.stdin), timeout=self.request.timeout
        )

        return ProcessResult(
            exit_code=self.process.returncode,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
        )

    async def terminate(self):
        self.process.terminate()

    async def kill(self):
        self.process.kill()

    async def stdout_lines(self):
        assert self.process.stdout is not None

        while line := await self.process.stdout.readline():
            yield line.decode(errors="replace").rstrip()

    async def stderr_lines(self):
        assert self.process.stderr is not None

        while line := await self.process.stderr.readline():
            yield line.decode(errors="replace").rstrip()


class ProcessExecutor(Protocol):
    async def execute(self, request: ProcessRequest) -> ProcessResult: ...

    async def start(self, request: ProcessRequest) -> RunningProcess: ...


class AsyncProcessExecutor(ProcessExecutor):
    async def execute(self, request: ProcessRequest):
        running_process = await self.start(request=request)

        return await running_process.wait()

    async def start(self, request: ProcessRequest) -> RunningProcess:
        process = await asyncio.create_subprocess_exec(
            str(request.executable),
            *request.args,
            cwd=request.cwd,
            env=request.env,
            stdin=asyncio.subprocess.PIPE if request.stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        return RunningProcess(process=process, request=request)
