import pytest

from simple_downloader.engines import EngineRegistry, NoEngineError
from simple_downloader.engines.hls import HlsEngine
from simple_downloader.engines.ytdlp import YtDlpEngine
from simple_downloader.domain.models import DownloadRequest


class _StubEngine:
    name = "stub"

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern

    def supports(self, url: str) -> bool:
        return self.pattern in url

    async def create_task(self, request: DownloadRequest):
        raise NotImplementedError


def test_registry_selects_first_matching_engine() -> None:
    registry = EngineRegistry()
    registry.register(_StubEngine(".m3u8"))
    registry.register(_StubEngine(""))

    engine = registry.engine_for("https://x/file.m3u8")
    assert engine.pattern == ".m3u8"


def test_registry_falls_back_to_later_engines() -> None:
    registry = EngineRegistry()
    registry.register(_StubEngine(".m3u8"))
    registry.register(_StubEngine(""))

    engine = registry.engine_for("https://x/file.mp4")
    assert engine.pattern == ""


def test_registry_raises_when_none_match() -> None:
    registry = EngineRegistry()
    with pytest.raises(NoEngineError):
        registry.engine_for("https://x/file.mp4")


def test_register_returns_engine() -> None:
    registry = EngineRegistry()
    engine = _StubEngine("x")
    assert registry.register(engine) is engine


def test_hls_engine_supports_m3u8_only() -> None:
    from simple_downloader.infra.http import AioHttpClient

    class DummyClient:
        async def get(self, url): ...

    engine = HlsEngine(DummyClient())
    assert engine.supports("https://x/master.m3u8")
    assert not engine.supports("https://x/video.mp4")


def test_hls_engine_supports_m3u8_with_query_string() -> None:
    class DummyClient:
        async def get(self, url): ...

    engine = HlsEngine(DummyClient())
    url = (
        "https://dash-s6-n50-fr-cdn.eporner.com/hls/v2/13288489-,240p-av1,"
        "360p-av1,480p-av1,720p-av1,1080p-av1,1440p-av1,.mp4.urlset/"
        "master.m3u8?hash=abc123&expires=1785945013&ip=148.222.112.229"
    )
    assert engine.supports(url)
    assert not engine.supports("https://x/video.mp4?s=1&t=2")


def test_origin_extracts_scheme_and_host() -> None:
    from simple_downloader.engines.common import origin

    assert origin("https://follame.top/a/b/master.m3u8") == "https://follame.top/"
    assert origin("http://files.example.com/seg/x.ts") == "http://files.example.com/"


def test_hls_engine_sends_referer_on_every_request() -> None:
    import asyncio

    from simple_downloader.engines.hls.engine import HlsEngine

    seen: list[tuple[str | None, str]] = []
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000,RESOLUTION=1920x1080\nv1.m3u8\n"
    media = "#EXTM3U\n#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"

    class RecordingClient:
        def with_referer(self, referer: str) -> "RefereredClient":
            return RefereredClient(referer)

        async def get(self, url: str) -> bytes:
            raise AssertionError("create_task debe usar el cliente con referer")

    class RefereredClient:
        def __init__(self, referer: str) -> None:
            self.referer = referer

        async def get(self, url: str) -> bytes:
            seen.append((self.referer, url))
            if url.startswith("https://follame.top/video/master.m3u8"):
                return master.encode()
            if url.endswith("v1.m3u8"):
                return media.encode()
            # primer segmento: probe -> TS estándar, se queda local
            return bytes([0x47]) * 377

    engine = HlsEngine(RecordingClient())
    request = DownloadRequest(url="https://follame.top/video/master.m3u8")

    task = asyncio.run(engine.create_task(request))
    assert task is not None
    assert len(seen) == 3, seen  # master + variante + segmento (probe)
    for referer, _url in seen:
        assert referer == "https://follame.top/"


def test_ytdlp_engine_is_catch_all() -> None:
    engine = YtDlpEngine(source_provider=object())  # type: ignore[arg-type]
    assert engine.supports("https://anything.else/file.mp4")


def _keys(fields) -> list[str]:
    return [field.key for field in fields]


def test_telegram_modal_fields_is_empty() -> None:
    from simple_downloader.engines.telegram import TelegramEngine

    engine = TelegramEngine(client_provider=object())  # type: ignore[arg-type]
    assert engine.modal_fields() == []


def test_http_modal_fields_headers_and_user_agent() -> None:
    from simple_downloader.engines.http import HttpEngine

    class DummyClient:
        async def get(self, url): ...

    engine = HttpEngine(DummyClient())
    assert _keys(engine.modal_fields()) == ["headers", "user_agent"]


def test_hls_modal_fields_includes_parallel_segments() -> None:
    from simple_downloader.engines.hls import HlsEngine

    class DummyClient:
        async def get(self, url): ...

    engine = HlsEngine(DummyClient())
    assert _keys(engine.modal_fields()) == [
        "headers",
        "user_agent",
        "max_parallel_segments",
    ]


def test_ytdlp_modal_fields_includes_cookies() -> None:
    from simple_downloader.engines.ytdlp import YtDlpEngine

    engine = YtDlpEngine(source_provider=object())  # type: ignore[arg-type]
    assert _keys(engine.modal_fields()) == ["headers", "cookies_path", "format_id"]


def test_format_field_options_default_first_and_sorted() -> None:
    from simple_downloader.engines.ytdlp.engine import format_field_options
    from simple_downloader.sources import Format

    options = format_field_options(
        [
            Format("137", "mp4", "1920x1080", 100),
            Format("136", "mp4", "1280x720", 50),
        ]
    )

    assert options[0].value == "best"
    assert options[0].label == "Vídeo + audio · Mejor combinado (sin ffmpeg)"
    assert options[1].label == "Vídeo + audio · 1080p (mp4)"
    assert options[1].value == (
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
    )
    assert options[2].label == "Solo vídeo · 1080p (mp4)"
    assert options[2].value == "bestvideo[height<=1080]/best[height<=1080]/best"
    assert options[3].label == "Vídeo + audio · 720p (mp4)"
    assert options[4].label == "Solo vídeo · 720p (mp4)"


def test_format_field_options_includes_audio_only_with_bitrate() -> None:
    from simple_downloader.engines.ytdlp.engine import format_field_options
    from simple_downloader.sources import Format

    options = format_field_options(
        [
            Format("140", "m4a", "audio only", 5, abr=128),
            Format("251", "webm", "audio only", 5, abr=160),
            Format("137", "mp4", "1920x1080", 100),
        ]
    )

    # Audio al final, mejor bitrate primero.
    assert options[-2].label == "Solo audio · webm · 160 kbps"
    assert options[-2].value == "251"
    assert options[-1].label == "Solo audio · m4a · 128 kbps"
    assert options[-1].value == "140"


def test_format_field_options_prefers_mp4_per_height() -> None:
    from simple_downloader.engines.ytdlp.engine import format_field_options
    from simple_downloader.sources import Format

    options = format_field_options(
        [
            Format("248", "webm", "1920x1080", 80),
            Format("137", "mp4", "1920x1080", 100),
        ]
    )

    assert "1080p (mp4)" in options[1].label
    assert "1080p (mp4)" in options[2].label


def test_resolution_height_parsing() -> None:
    from simple_downloader.engines.ytdlp.engine import _resolution_height

    assert _resolution_height("1920x1080") == 1080
    assert _resolution_height("720") == 720
    assert _resolution_height("audio only") is None
    assert _resolution_height("") is None


def test_ytdlp_formats_parses_abr() -> None:
    import asyncio
    import json
    from types import SimpleNamespace

    from simple_downloader.executor import Executable, ExecutableStatus
    from simple_downloader.sources import YtDlpSource

    class FakeExec:
        async def execute(self, request):
            return SimpleNamespace(
                exit_code=0,
                stdout=json.dumps(
                    {
                        "formats": [
                            {
                                "format_id": "140",
                                "ext": "m4a",
                                "resolution": "audio only",
                                "filesize_approx": 5,
                                "abr": 128,
                            },
                            {
                                "format_id": "137",
                                "ext": "mp4",
                                "resolution": "1920x1080",
                                "filesize_approx": 100,
                            },
                        ]
                    }
                ),
            )

    source = YtDlpSource(
        executable=Executable(
            status=ExecutableStatus.ACTIVE, name="yt-dlp", path="/usr/bin/yt-dlp"
        ),
        executor=FakeExec(),
    )

    formats = asyncio.run(source.formats("https://x/v"))

    assert formats[0].format_id == "140"
    assert formats[0].abr == 128
    assert formats[1].abr is None


def test_ytdlp_modal_options_from_source_with_cache() -> None:
    import asyncio

    from simple_downloader.engines.ytdlp import YtDlpEngine
    from simple_downloader.sources import Format

    calls = []

    class FakeFormatsSource:
        async def formats(self, url):
            calls.append(url)
            return [
                Format("137", "mp4", "1920x1080", 100),
                Format("140", "m4a", "audio only", 5, abr=128),
            ]

    class FakeProvider:
        def get_source(self, executable_name):
            return FakeFormatsSource()

    engine = YtDlpEngine(source_provider=FakeProvider())

    first = asyncio.run(engine.modal_options("https://x/video"))
    second = asyncio.run(engine.modal_options("https://x/video"))

    assert [option.value for option in first["format_id"]] == [
        "best",
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "bestvideo[height<=1080]/best[height<=1080]/best",
        "140",
    ]
    assert second == first
    assert calls == ["https://x/video"]  # cacheado: una sola llamada


def test_hls_engine_validate_accepts_real_playlist() -> None:
    import asyncio

    from simple_downloader.engines.hls.engine import HlsEngine

    class PlaylistClient:
        async def get(self, url: str) -> bytes:
            return b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nv1.m3u8\n"

    engine = HlsEngine(PlaylistClient())

    assert asyncio.run(engine.validate("https://x/main.m3u8")) is None


def test_hls_engine_validate_rejects_non_playlist() -> None:
    import asyncio

    import pytest

    from simple_downloader.engines.hls.engine import HlsEngine

    class HtmlClient:
        async def get(self, url: str) -> bytes:
            return b"<html>not a playlist</html>"

    engine = HlsEngine(HtmlClient())

    with pytest.raises(ValueError, match="m3u8"):
        asyncio.run(engine.validate("https://x/main.m3u8"))
