from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

from simple_downloader.domain.models import DownloadRequest
from simple_downloader.domain.options import (
    HEADERS_FIELD,
    USER_AGENT_FIELD,
    ModalField,
)
from simple_downloader.domain.protocols import DownloadTask, Engine, HttpClient
from simple_downloader.engines.common import (
    http_with_context,
    resolve_output,
)
from simple_downloader.engines.http.task import HttpDownloadTask

_DIRECT_EXTENSIONS = frozenset(
    {
        ".mp4",
        ".webm",
        ".mkv",
        ".avi",
        ".mov",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".ts",
        ".mp3",
        ".m4a",
        ".aac",
        ".wav",
        ".flac",
        ".ogg",
        ".zip",
        ".rar",
        ".7z",
        ".pdf",
        ".epub",
    }
)

_QUERY_MEDIA_PARAMS = frozenset(
    {"file", "url", "src", "source", "media", "video", "download", "link"}
)


def _media_from_query(query: str) -> tuple[str, str] | None:
    """Busca un archivo directo escondido en los parámetros de la query.

    Cubre URLs "wrapper" (remote_control.php?file=video.mp4&token=...) cuyo
    path es un script pero el backend sirve el recurso directo (el header
    content-type lo confirma). Devuelve (basename, extension) o None.
    """
    for key, value in parse_qsl(query):
        if key not in _QUERY_MEDIA_PARAMS:
            continue
        path = Path(unquote(value))
        suffix = path.suffix.lower()
        if suffix in _DIRECT_EXTENSIONS:
            return path.name, suffix
    return None


class HttpEngine(Engine):
    """Descarga directa con GET streaming: para URLs de archivos
    con extensión, sin depender de yt-dlp ni de su extractor generic."""

    name = "http"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def supports(self, url: str) -> bool:
        parts = urlsplit(url)
        if Path(parts.path).suffix.lower() in _DIRECT_EXTENSIONS:
            return True
        return _media_from_query(parts.query) is not None

    def modal_fields(self) -> list[ModalField]:
        return [HEADERS_FIELD, USER_AGENT_FIELD]

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        http = http_with_context(self._http, request.url, request.context)
        parts = urlsplit(request.url)
        query_media = _media_from_query(parts.query)
        if query_media is not None:
            name, ext = query_media
        else:
            name = Path(parts.path).name or f"file-{request.url[-8:]}"
            ext = Path(parts.path).suffix.lstrip(".") or None
        out_file = resolve_output(
            request.url,
            request.output,
            default_name=name,
            ext=ext,
            media={"title": name},
        )
        return HttpDownloadTask(url=request.url, out_file=out_file, http=http)

    async def validate(self, url: str) -> None:
        """Verificación ligera: el servidor responde al recurso.

        Lanza en 4xx/5xx/errores de red; si el cliente no soporta
        `check` (fakes), se asume que es descargable."""
        http = http_with_context(self._http, url, None)
        check = getattr(http, "check", None)
        if check is None:
            return
        await check(url)
