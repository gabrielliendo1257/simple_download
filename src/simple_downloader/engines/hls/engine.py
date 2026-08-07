from __future__ import annotations

from urllib.parse import urlsplit
from uuid import uuid4

from simple_downloader.domain.models import DownloadRequest
from simple_downloader.domain.options import (
    FORMAT_FIELD,
    HEADERS_FIELD,
    PARALLEL_SEGMENTS_FIELD,
    USER_AGENT_FIELD,
    FieldOption,
    ModalField,
)
from simple_downloader.domain.protocols import DownloadTask, Engine, HttpClient
from simple_downloader.engines.common import http_with_context, resolve_output
from simple_downloader.engines.hls.fetch import SegmentFetcher
from simple_downloader.engines.hls.models import HlsPlaylist, Variant
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
        self._variant_cache: dict[str, tuple[Variant, ...]] = {}

    def supports(self, url: str) -> bool:
        # Evaluar sobre el path, no la URL completa: las playlists reales
        # llevan query string (hash, expires, ip) después del .m3u8.
        return urlsplit(url).path.lower().endswith(".m3u8")

    def modal_fields(self) -> list[ModalField]:
        return [HEADERS_FIELD, USER_AGENT_FIELD, PARALLEL_SEGMENTS_FIELD, FORMAT_FIELD]

    async def modal_options(self, url: str) -> dict[str, list[FieldOption]]:
        """Variantes reales de una master playlist (bandwidth/resolución).

        Vacío si la URL es una media playlist directa (sin variantes):
        el campo CHOICE no se muestra y se descarga tal cual. Las
        variantes se cachean por URL para no re-fetchear al reabrir el
        modal; el fetch de verdad (con contexto) ocurre en create_task.
        """
        cached = self._variant_cache.get(url)
        if cached is not None:
            return {"format_id": variant_field_options(cached)}

        http = http_with_context(self._http, url, None)
        text = (await http.get(url)).decode(errors="replace")
        if "#EXT-X-STREAM-INF" not in text:
            return {}
        variants = parse_master(url, text)
        self._variant_cache[url] = variants
        return {"format_id": variant_field_options(variants)}

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        http = http_with_context(self._http, request.url, request.context)
        playlist = await self._resolve_playlist(
            request.url, http, _bandwidth_from_format_id(request.format_id)
        )

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

    async def _resolve_playlist(
        self,
        url: str,
        http: HttpClient,
        bandwidth: int | None = None,
    ) -> HlsPlaylist:
        text = (await http.get(url)).decode(errors="replace")

        if "#EXT-X-STREAM-INF" not in text:
            return parse_media_playlist(url, text)

        variants = parse_master(url, text)
        if not variants:
            raise ValueError(f"master playlist has no variants: {url}")

        # El usuario eligió una variante en el modal (format_id =
        # bandwidth); si no se reconoce, se cae a la mejor.
        selected = None
        if bandwidth is not None:
            selected = next(
                (v for v in variants if v.bandwidth == bandwidth), None
            )
        if selected is None:
            selected = max(variants, key=lambda variant: variant.bandwidth)

        media_text = (await http.get(selected.url)).decode(errors="replace")
        return parse_media_playlist(selected.url, media_text, selected.resolution)


def variant_field_options(variants: tuple[Variant, ...]) -> list[FieldOption]:
    """Variantes de una master playlist como opciones del modal.

    Ordenadas por bandwidth desc (la primera = la mejor = selección por
    defecto del Select); el value es el bandwidth, que crea_task vuelve
    a traducir a variante.
    """
    return [
        FieldOption(_variant_label(variant), str(variant.bandwidth))
        for variant in sorted(variants, key=lambda v: v.bandwidth, reverse=True)
    ]


def _variant_label(variant: Variant) -> str:
    """Etiqueta legible: "1080p · 2.6 Mbps", "Vídeo · 384 kbps", ..."""
    bandwidth = variant.bandwidth
    bitrate = (
        f"{bandwidth / 1_000_000:.1f} Mbps"
        if bandwidth >= 1_000_000
        else f"{bandwidth // 1000} kbps"
    )
    if variant.resolution:
        height = variant.resolution.split("x")[-1]
        return f"{height}p · {bitrate}"
    return f"Vídeo · {bitrate}"


def _bandwidth_from_format_id(format_id: str | None) -> int | None:
    """El value del Select es el bandwidth; None si no es reconocible."""
    if format_id is None or not format_id.isdigit():
        return None
    return int(format_id)
