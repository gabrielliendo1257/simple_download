from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from enum import Enum, StrEnum, auto
from pathlib import Path

from simple_downloader.process import ProcessExecutor, ProcessRequest


@dataclass(frozen=True)
class Executable:
    status: ExecutableStatus
    name: str | None = None
    path: Path | None = None
    version: str | None = None


class ExecutableStatus(StrEnum):
    ACTIVE = auto()
    NOT_FOUND = auto()
    ERROR = auto()
    INVALID = auto()


class ExecutableName(Enum):
    YT_DLP = "yt-dlp"
    GALLERY_DL = "gallery-dl"


@dataclass(frozen=True)
class ExecutableSpec:
    name: str
    version_arg: list[str] = field(default_factory=list)


class ExecutorDetector:
    def __init__(self, executor: ProcessExecutor) -> None:
        self._executor = executor

    async def detect(self, executable_spec: ExecutableSpec) -> Executable:
        executable_path: str | None = shutil.which(executable_spec.name)
        if executable_path is None:
            return Executable(
                name=executable_spec.name, status=ExecutableStatus.NOT_FOUND
            )

        executable: Path = Path(executable_path)

        try:
            request = ProcessRequest(executable=executable, args=["--version"])
            result = await self._executor.execute(request=request)

            if result.exit_code != 0:
                return Executable(
                    name=executable_spec.name,
                    path=executable,
                    version=None,
                    status=ExecutableStatus.INVALID,
                )

            return Executable(
                name=executable_spec.name,
                path=executable,
                version=result.stdout.strip(),
                status=ExecutableStatus.ACTIVE,
            )
        except Exception:
            return Executable(
                name=executable_spec.name,
                path=Path(executable_path) if "executable_path" in locals() else None,
                version=None,
                status=ExecutableStatus.ERROR,
            )


class ExecutorRegistry:
    def __init__(self) -> None:
        self.executors: dict[str, Executable] = {}

    def register(self, executable: Executable):
        if executable.name is not None:
            self.executors[executable.name] = executable

    def get_executor(self, executable_name: ExecutableName) -> Executable:
        return self.executors.get(
            executable_name.value, Executable(status=ExecutableStatus.ERROR)
        )
