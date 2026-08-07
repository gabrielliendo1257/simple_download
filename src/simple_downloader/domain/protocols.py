from __future__ import annotations

from typing import AsyncIterator, Protocol
from uuid import UUID

from simple_downloader.domain.models import (
    DownloadJob,
    DownloadProgress,
    DownloadRequest,
    DownloadResult,
)
from simple_downloader.domain.options import FieldOption, ModalField


class DownloadTask(Protocol):
    """Unidad de trabajo: el scheduler no sabe si es subprocess o nativo."""

    def progress(self) -> AsyncIterator[DownloadProgress]: ...

    async def cancel(self) -> None: ...

    async def pause(self) -> None: ...

    async def finalize(self) -> DownloadResult: ...


class Engine(Protocol):
    """Un Strategy por protocolo de descarga (http directo, hls, yt-dlp...)."""

    name: str

    def supports(self, url: str) -> bool: ...

    def modal_fields(self) -> list[ModalField]:
        """Campos del modal de añadir que le sirven a este engine.

        El modal se construye según la especificación del engine que
        resolvió la URL: telegram no pide cookies, yt-dlp no pide
        segmentos, etc. Las keys usan el vocabulario compartido de
        `domain/options` (la TUI lo traduce a DownloadContext)."""
        ...

    async def modal_options(self, url: str) -> dict[str, list[FieldOption]]:
        """Opciones dinámicas por URL para los campos CHOICE.

        Ej. yt-dlp devuelve las resoluciones reales del video (spec en
        runtime): el modal las muestra en un Select. Vacío = el campo
        CHOICE no se renderiza."""
        ...

    async def create_task(self, request: DownloadRequest) -> DownloadTask: ...

    async def validate(self, url: str) -> None:
        """Verificación ligera de que la URL es genuina y descargable.

        Sin metadata (GET de la playlist, range check, etc.). Lanza
        con un mensaje legible si la URL no es descargable."""
        ...


class HttpClient(Protocol):
    async def get(self, url: str) -> bytes: ...


class DownloadJobRepository(Protocol):
    async def save(self, job: DownloadJob) -> None: ...

    async def find(self, job_id: UUID) -> DownloadJob | None: ...

    async def list(self) -> list[DownloadJob]: ...

    async def delete(self, job_id: UUID) -> None: ...
