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


def test_origin_extracts_scheme_and_host() -> None:
    from simple_downloader.engines.common import origin

    assert origin("https://follame.top/a/b/master.m3u8") == "https://follame.top/"
    assert origin("http://files.example.com/seg/x.ts") == "http://files.example.com/"


def test_hls_engine_sends_referer_on_every_request() -> None:
    import asyncio

    from simple_downloader.engines.hls.engine import HlsEngine

    seen: list[tuple[str | None, str]] = []

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
            return b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nv1.m3u8\n"

    engine = HlsEngine(RecordingClient())
    request = DownloadRequest(url="https://follame.top/video/master.m3u8")

    task = asyncio.run(engine.create_task(request))
    assert task is not None
    assert len(seen) == 2, seen  # playlist master + playlist de variante
    for referer, _url in seen:
        assert referer == "https://follame.top/"


def test_ytdlp_engine_is_catch_all() -> None:
    engine = YtDlpEngine(source_provider=object())  # type: ignore[arg-type]
    assert engine.supports("https://anything.else/file.mp4")