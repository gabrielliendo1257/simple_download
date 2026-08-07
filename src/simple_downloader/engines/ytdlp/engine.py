from __future__ import annotations

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
        self.cookies_from_browser = cookies_from_browser
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
            cookies_from_browser=self.cookies_from_browser,
        )
        return SubprocessTaskAdapter(runner)

    async def metadata(self, url: str) -> VideoMetadata:
        """Metadata con las cookies globales (x.com no da datos sin ellas)."""
        source = self._source_provider.get_source(ExecutableName.YT_DLP)
        return await source.metadata(
            url, cookies_from_browser=self.cookies_from_browser
        )

    async def validate(self, url: str) -> None:
        # La metadata de yt-dlp ya validó la URL: si falló, se cae al
        # nombre por defecto; nada más que comprobar aquí.
        return None


_EXT_PRIORITY = {"mp4": 0, "m4a": 0, "webm": 1}


def _video_audio_selector(height: int) -> str:
    """Vídeo + audio hasta `height`: merge con la mejor pista de audio,
    con fallback a un archivo combinado y luego al mejor."""
    return f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"


def _video_only_selector(height: int) -> str:
    """Solo vídeo hasta `height` (sin pista de audio), con fallback a
    un combinado por si no hay formato solo-vídeo."""
    return f"bestvideo[height<={height}]/best[height<={height}]/best"


def format_field_options(formats: list[Format]) -> list[FieldOption]:
    """Convierte los formatos de yt-dlp en opciones para el modal.

    Un solo Select con las tres variantes que el usuario puede querer:
    - Vídeo + audio (default: "best", un archivo combinado sin ffmpeg;
      por altura: merge bestvideo+bestaudio con fallbacks).
    - Solo vídeo por altura (bestvideo).
    - Solo audio: los formatos de audio directos de la URL (sin -x).
    Ordenadas: combinado/mejor primero, alturas desc, audio al final.
    """
    options = [FieldOption("Vídeo + audio · Mejor combinado (sin ffmpeg)", "best")]

    video_by_height: dict[int, Format] = {}
    audio_by_ext: dict[str, Format] = {}
    for fmt in formats:
        height = _resolution_height(fmt.resolution)
        if height is not None:
            current = video_by_height.get(height)
            priority = _EXT_PRIORITY.get(fmt.ext, 2)
            if current is None or priority < _EXT_PRIORITY.get(current.ext, 2):
                video_by_height[height] = fmt
        elif fmt.ext:
            # audio only: la mejor calidad por contenedor
            current = audio_by_ext.get(fmt.ext)
            if current is None or (fmt.abr or 0) > (current.abr or 0):
                audio_by_ext[fmt.ext] = fmt

    for height in sorted(video_by_height, reverse=True):
        fmt = video_by_height[height]
        options.append(
            FieldOption(f"Vídeo + audio · {height}p ({fmt.ext})", _video_audio_selector(height))
        )
        options.append(
            FieldOption(f"Solo vídeo · {height}p ({fmt.ext})", _video_only_selector(height))
        )

    for fmt in sorted(audio_by_ext.values(), key=lambda f: -(f.abr or 0)):
        abr = f" · {fmt.abr} kbps" if fmt.abr else ""
        options.append(FieldOption(f"Solo audio · {fmt.ext}{abr}", fmt.format_id))
    return options


def _resolution_height(resolution: str) -> int | None:
    """Altura de "1920x1080" -> 1080; "720" -> 720; "audio only" -> None."""
    digits = [int(part) for part in resolution.replace("x", " ").split() if part.isdigit()]
    if not digits:
        return None
    return digits[-1] if len(digits) > 1 else digits[0]
