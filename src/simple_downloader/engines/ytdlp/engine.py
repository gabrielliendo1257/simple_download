from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from simple_downloader.domain.models import DownloadOutput, DownloadRequest
from simple_downloader.domain.options import (
    COOKIES_FIELD,
    FORMAT_FIELD,
    HEADERS_FIELD,
    FieldOption,
    ModalField,
)
from simple_downloader.domain.protocols import DownloadTask, Engine
from simple_downloader.engines.ytdlp.adapter import SubprocessTaskAdapter
from simple_downloader.executor import ExecutableName
from simple_downloader.sources import Format, SourceProvider, VideoMetadata

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
        self._format_cache: dict[str, tuple[FieldOption, ...]] = {}

    def supports(self, url: str) -> bool:
        return True

    def modal_fields(self) -> list[ModalField]:
        return [HEADERS_FIELD, COOKIES_FIELD, FORMAT_FIELD]

    async def modal_options(self, url: str) -> dict[str, list[FieldOption]]:
        """Resoluciones reales de la URL (metadata ya resuelta).

        La primera vez invoca a yt-dlp (`--dump-single-json`); el
        resultado se cachea por URL para que reabrir el modal no cueste
        otra llamada. Vacío si yt-dlp no da formatos con vídeo."""
        cached = self._format_cache.get(url)
        if cached is not None:
            return {"format_id": list(cached)}
        source = self._source_provider.get_source(ExecutableName.YT_DLP)
        formats = await source.formats(url)
        options = tuple(format_field_options(formats))
        self._format_cache[url] = options
        return {"format_id": list(options)}

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


_EXT_PRIORITY = {"mp4": 0, "m4a": 0, "webm": 1}


def format_field_options(formats: list[Format]) -> list[FieldOption]:
    """Convierte los formatos de yt-dlp en opciones para el modal.

    Reglas:
    - siempre hay un default: "Mejor combinado" (`best`, video+audio en
      un archivo, sin necesidad de ffmpeg para unir).
    - solo formatos con vídeo (se descartan los "audio only").
    - una opción por altura de resolución, prefiriendo mp4/m4a.
    - ordenadas de mayor a menor resolución.
    """
    options = [FieldOption("Mejor combinado (sin ffmpeg)", "best")]

    best_by_height: dict[int, Format] = {}
    for fmt in formats:
        height = _resolution_height(fmt.resolution)
        if height is None:
            continue  # audio-only u otro formato sin vídeo
        current = best_by_height.get(height)
        priority = _EXT_PRIORITY.get(fmt.ext, 2)
        if current is None or priority < _EXT_PRIORITY.get(current.ext, 2):
            best_by_height[height] = fmt

    for height in sorted(best_by_height, reverse=True):
        fmt = best_by_height[height]
        size = f" · {_human_size(fmt.filesize_approx)}" if fmt.filesize_approx else ""
        options.append(FieldOption(f"{height}p ({fmt.ext}){size}", fmt.format_id))
    return options


def _resolution_height(resolution: str) -> int | None:
    """Altura de "1920x1080" -> 1080; "720" -> 720; "audio only" -> None."""
    digits = [int(part) for part in resolution.replace("x", " ").split() if part.isdigit()]
    if not digits:
        return None
    return digits[-1] if len(digits) > 1 else digits[0]


def _human_size(bytes_: int | None) -> str:
    if not bytes_:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_ < 1024:
            return f"{bytes_:.0f} {unit}"
        bytes_ //= 1024
    return f"{bytes_} GB"
