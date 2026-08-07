from __future__ import annotations

from urllib.parse import urlsplit
from uuid import uuid4

from simple_downloader.domain.models import DownloadRequest
from simple_downloader.domain.options import (
    HEADERS_FIELD,
    PARALLEL_SEGMENTS_FIELD,
    USER_AGENT_FIELD,
    FieldOption,
    ModalField,
)
from simple_downloader.domain.protocols import DownloadTask, Engine, HttpClient
from simple_downloader.engines.common import http_with_context, resolve_output
from simple_downloader.engines.hls.fetch import SegmentFetcher
from simple_downloader.engines.hls.models import HlsPlaylist
from simple_downloader.engines.hls.parser import parse_master, parse_media_playlist
from simple_downloader.engines.hls.probe import SegmentFormat, probe_segment
from simple_downloader.engines.hls.task import HlsTask


class HlsEngine(Engine):
    """Engine para playlists .m3u8 con descarga nativa.

    Descarga con HlsTask todos los formatos reales: TS, fMP4 y los
    streams ofuscados (segmentos envueltos en PNG). Un probe del primer
    segmento clasifica el formato y decide el nombre/extensión de salida;
    si el segmento no se reconoce, falla con un error claro.
    """

    name = "hls"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def supports(self, url: str) -> bool:
        # Evaluar sobre el path, no la URL completa: las playlists reales
        # llevan query string (hash, expires, ip) después del .m3u8.
        return urlsplit(url).path.lower().endswith(".m3u8")

    def modal_fields(self) -> list[ModalField]:
        return [HEADERS_FIELD, USER_AGENT_FIELD, PARALLEL_SEGMENTS_FIELD]

    async def modal_options(self, url: str) -> dict[str, list[FieldOption]]:
        return {}

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        http = http_with_context(self._http, request.url, request.context)
        playlist = await self._resolve_playlist(request.url, http)

        if not playlist.segments:
            raise ValueError(f"playlist has no segments: {request.url}")

        fmt = await probe_segment(http, playlist.segments[0].uri)
        if fmt is SegmentFormat.UNKNOWN:
            raise ValueError(
                f"no se reconoce el formato del primer segmento de {request.url}"
            )

        ext = "mp4" if fmt in (SegmentFormat.PNG_WRAPPED, SegmentFormat.FMP4) else "ts"
        out_file = resolve_output(
            request.url,
            request.output,
            default_name=f"video-{uuid4().hex}.{ext}",
            ext=ext,
            media={"resolution": playlist.resolution or ""},
        )
        max_parallel = (
            request.context.max_parallel_segments if request.context is not None else 6
        )
        return HlsTask(
            out_file=out_file,
            fetcher=SegmentFetcher(http),
            segments=playlist.segments,
            max_parallel=max_parallel,
            init_uri=playlist.init_uri,
        )

    async def validate(self, url: str) -> None:
        """Verificación ligera: la URL responde y es una playlist m3u8 real.

        No se resuelven variantes ni se probea el primer segmento: eso
        queda para create_task."""
        http = http_with_context(self._http, url, None)
        text = (await http.get(url)).decode(errors="replace")
        if "#EXTM3U" not in text:
            raise ValueError(f"no es una playlist m3u8 válida: {url}")

    async def _resolve_playlist(self, url: str, http: HttpClient) -> HlsPlaylist:
        text = (await http.get(url)).decode(errors="replace")

        if "#EXT-X-STREAM-INF" not in text:
            return parse_media_playlist(url, text)

        variants = parse_master(url, text)
        if not variants:
            raise ValueError(f"master playlist has no variants: {url}")

        best = max(variants, key=lambda variant: variant.bandwidth)
        media_text = (await http.get(best.url)).decode(errors="replace")
        return parse_media_playlist(best.url, media_text, best.resolution)
