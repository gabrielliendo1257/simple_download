from typing import Protocol
from uuid import UUID

from simple_downloader.domain.models import DownloadJob
from simple_downloader.domain.protocols import DownloadJobRepository


class InMemoryRepository(DownloadJobRepository):
    def __init__(self) -> None:
        self._jobs: dict[UUID, DownloadJob] = {}

    async def save(self, job: DownloadJob) -> None:
        self._jobs[job.id] = job

    async def find(self, id: UUID) -> DownloadJob | None:
        return self._jobs.get(id)

    async def list(self) -> list[DownloadJob]:
        return list(self._jobs.values())