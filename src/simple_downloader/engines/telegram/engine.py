from __future__ import annotations

from pathlib import Path

from simple_downloader.domain.models import DownloadRequest
from simple_downloader.domain.protocols import DownloadTask, Engine
from simple_downloader.engines.common import resolve_output
from simple_downloader.engines.telegram.client import TelegramClientProvider
from simple_downloader.engines.telegram.links import TelegramLink, parse_link
from simple_downloader.engines.telegram.task import TelegramDownloadTask


class TelegramEngine(Engine):
    """Descarga el media de un mensaje de Telegram con Telethon.

    Un solo segmento: Telethon descarga el archivo completo con su
    propio pipe optimizado; el paralelismo no aporta nada aquí.
    """

    name = "telegram"

    def __init__(self, client_provider: TelegramClientProvider) -> None:
        self._provider = client_provider

    def supports(self, url: str) -> bool:
        return parse_link(url) is not None

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        link = parse_link(request.url)
        if link is None:
            raise ValueError(f"no es un link de Telegram: {request.url}")

        message = await self._provider.get_message(link.peer, link.message_id)
        if message is None or not getattr(message, "media", None):
            raise ValueError(f"el mensaje {link.message_id} no tiene media")

        default_name = _document_name(link, message)
        ext = Path(default_name).suffix.lstrip(".") or None
        out_file = resolve_output(
            request.url,
            request.output,
            default_name=default_name,
            ext=ext,
            media={"title": Path(default_name).stem},
        )
        return TelegramDownloadTask(
            message=message,
            out_file=out_file,
            provider=self._provider,
            title=default_name,
        )

    async def validate(self, url: str) -> None:
        """Verificación ligera: el mensaje existe y tiene media."""
        link = parse_link(url)
        if link is None:
            raise ValueError(f"no es un link de Telegram: {url}")
        message = await self._provider.get_message(link.peer, link.message_id)
        if message is None or not getattr(message, "media", None):
            raise ValueError(f"el mensaje {link.message_id} no tiene media")


def _document_name(link: TelegramLink, message) -> str:
    """Nombre del documento tal como lo reporta Telegram.

    Si el documento no trae nombre, se usa `telegram-<id>.<ext>` como
    default (extensión del media si la conoce Telethon).
    """
    file = getattr(message, "file", None)
    file_name = getattr(file, "name", None) if file is not None else None
    if isinstance(file_name, str) and file_name:
        return file_name

    media_ext = getattr(file, "ext", None) if file is not None else None
    suffix = media_ext.lstrip(".") if isinstance(media_ext, str) and media_ext else ""
    return f"telegram-{link.message_id}" + (f".{suffix}" if suffix else "")
