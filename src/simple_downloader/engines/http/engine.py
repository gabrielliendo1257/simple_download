from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from simple_downloader.domain.models import DownloadRequest
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


class HttpEngine(Engine):
    """Descarga directa con GET streaming: para URLs de archivos
    con extensión, sin depender de yt-dlp ni de su extractor generic."""

    name = "http"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def supports(self, url: str) -> bool:
        path = urlsplit(url).path
        return Path(path).suffix.lower() in _DIRECT_EXTENSIONS

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        http = http_with_context(self._http, request.url, request.context)
        name = Path(urlsplit(request.url).path).name or f"file-{request.url[-8:]}"
        ext = Path(urlsplit(request.url).path).suffix.lstrip(".") or None
        out_file = resolve_output(
            request.url,
            request.output,
            default_name=name,
            ext=ext,
            media={"title": name},
        )
        return HttpDownloadTask(url=request.url, out_file=out_file, http=http)
