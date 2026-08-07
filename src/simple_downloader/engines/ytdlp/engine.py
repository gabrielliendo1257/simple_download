from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from simple_downloader.domain.models import DownloadOutput, DownloadRequest
from simple_downloader.domain.protocols import DownloadTask, Engine
from simple_downloader.engines.ytdlp.adapter import SubprocessTaskAdapter
from simple_downloader.executor import ExecutableName
from simple_downloader.sources import SourceProvider, VideoMetadata

_YTDLP_PLACEHOLDERS = {
    "{title}": "%(title)s",
    "{id}": "%(id)s",
    "{ext}": "%(ext)s",
    "{resolution}": "%(height)sp",
    "{date}": "%(upload_date)s",
}


def ytdlp_output(output: DownloadOutput | None) -> str | None:
    """Traduce DownloadOutput a un argumento -o para yt-dlp.

    None -> yt-dlp decide (default %(title)s.%(ext)s en cwd).
    """
    if output is None:
        return None

    directory = str(output.directory).rstrip("/") + "/"

    if output.filename is not None:
        return f"{directory}{output.filename}"

    if output.template is not None:
        translated = output.template
        for key, value in _YTDLP_PLACEHOLDERS.items():
            translated = translated.replace(key, value)
        return f"{directory}{translated}"

    return f"{directory}%(title)s.%(ext)s"


class YtDlpEngine(Engine):
    """Adapter a yt-dlp: catch-all, se registra al final del registry."""

    name = "yt-dlp"

    def __init__(
        self, source_provider: SourceProvider, cookies_from_browser: str | None = None
    ) -> None:
        self._source_provider = source_provider
        self._cookies_from_browser = cookies_from_browser

    def supports(self, url: str) -> bool:
        return True

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        source = self._source_provider.get_source(ExecutableName.YT_DLP)
        context = request.context
        runner = await source.download(
            url=request.url,
            output=ytdlp_output(request.output),
            format_id=request.format_id,
            extract_audio=request.extract_audio,
            resume=request.resume,
            headers=context.headers if context else None,
            cookies_path=context.cookies_path if context else None,
            cookies_from_browser=self._cookies_from_browser,
        )
        return SubprocessTaskAdapter(runner)

    async def metadata(self, url: str) -> VideoMetadata:
        """Metadata con las cookies globales (x.com no da datos sin ellas)."""
        source = self._source_provider.get_source(ExecutableName.YT_DLP)
        return await source.metadata(
            url, cookies_from_browser=self._cookies_from_browser
        )

    async def validate(self, url: str) -> None:
        # La metadata de yt-dlp ya validó la URL: si falló, se cae al
        # nombre por defecto; nada más que comprobar aquí.
        return None
