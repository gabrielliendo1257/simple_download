from __future__ import annotations

from uuid import uuid4

from simple_downloader.domain.models import DownloadRequest
from simple_downloader.domain.protocols import DownloadTask, Engine, HttpClient
from simple_downloader.engines.common import http_with_context, resolve_output
from simple_downloader.engines.hls.fetch import SegmentFetcher
from simple_downloader.engines.hls.models import HlsPlaylist
from simple_downloader.engines.hls.parser import parse_master, parse_media_playlist
from simple_downloader.engines.hls.task import HlsTask


class HlsEngine(Engine):
    name = "hls"

    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def supports(self, url: str) -> bool:
        return url.lower().endswith(".m3u8")

    async def create_task(self, request: DownloadRequest) -> DownloadTask:
        http = http_with_context(self._http, request.url, request.context)
        playlist = await self._resolve_playlist(request.url, http)
        out_file = resolve_output(
            request.url,
            request.output,
            default_name=f"video-{uuid4().hex}.ts",
            ext="ts",
            media={"resolution": playlist.resolution or ""},
        )
        max_parallel = (
            request.context.max_parallel_segments
            if request.context is not None
            else 6
        )
        return HlsTask(
            out_file=out_file,
            fetcher=SegmentFetcher(http),
            segments=playlist.segments,
            max_parallel=max_parallel,
            init_uri=playlist.init_uri,
        )

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