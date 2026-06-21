from typing import Protocol
from uuid import UUID

from simple_downloader.app.manager import DownloadJob


class DownloadJobRepository(Protocol):
    async def save(
        self,
        job: DownloadJob,
    ): ...

    async def find(
        self,
        id: UUID,
    ) -> DownloadJob: ...

    async def list(self) -> list[DownloadJob]: ...


class InMemoryRepository(DownloadJobRepository):
    async def save(self, job: DownloadJob):
        return await super().save(job)

    async def find(self, id: UUID) -> DownloadJob:
        return await super().find(id)

    async def list(self) -> list[DownloadJob]:
        return await super().list()
