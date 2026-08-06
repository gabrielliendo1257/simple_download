from pathlib import Path

from simple_downloader.domain.models import DownloadContext, DownloadOutput
from simple_downloader.engines.common import (
    http_with_context,
    origin,
    resolve_output,
)


class FakeClient:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers

    def with_headers(self, headers: dict[str, str]) -> "FakeClient":
        return FakeClient(headers)

    def with_referer(self, referer: str) -> "FakeClient":
        return FakeClient({**(self.headers or {}), "referer": referer})

    async def get(self, url: str) -> bytes:
        return b""


def test_origin_extracts_base_url() -> None:
    assert origin("https://follame.top/a/master.m3u8") == "https://follame.top/"
    assert origin("http://files.example.com/seg/x") == "http://files.example.com/"


def test_resolve_output_default_name() -> None:
    assert resolve_output("https://x/file.mp4", None, default_name="video.ts") == Path(
        "video.ts"
    )


def test_resolve_output_filename_in_directory(tmp_path) -> None:
    out = DownloadOutput(directory=tmp_path, filename="mi-video.mp4")
    resolved = resolve_output("https://x/file.mp4", out, default_name="x.mp4")
    assert resolved == tmp_path / "mi-video.mp4"
    assert resolved.parent.exists()


def test_resolve_output_adds_missing_extension(tmp_path) -> None:
    out = DownloadOutput(directory=tmp_path, filename="mi-video")
    resolved = resolve_output("https://x/file.mp4", out, default_name="x", ext="mp4")
    assert resolved == tmp_path / "mi-video.mp4"


def test_resolve_output_template_with_media(tmp_path) -> None:
    out = DownloadOutput(directory=tmp_path, template="{title}_{resolution}.{ext}")
    resolved = resolve_output(
        "https://x/master.m3u8",
        out,
        default_name="video.ts",
        ext="ts",
        media={"title": "capitulo", "resolution": "720p"},
    )
    assert resolved == tmp_path / "capitulo_720p.ts"


def test_http_with_context_derives_referer_when_absent() -> None:
    client = http_with_context(FakeClient(), "https://follame.top/a/x.m3u8")
    assert client.headers == {"referer": "https://follame.top/"}


def test_http_with_context_uses_explicit_referer() -> None:
    ctx = DownloadContext(referer="https://mipagina.example/player")
    client = http_with_context(FakeClient(), "https://follame.top/a/x.m3u8", ctx)
    assert client.headers == {"referer": "https://mipagina.example/player"}


def test_http_with_context_merges_headers_and_ua() -> None:
    ctx = DownloadContext(
        referer="https://x.example/",
        user_agent="Mozilla/5.0",
        headers={"cookie": "a=1"},
    )
    client = http_with_context(FakeClient(), "https://follame.top/a/x.m3u8", ctx)
    assert client.headers == {
        "referer": "https://x.example/",
        "user-agent": "Mozilla/5.0",
        "cookie": "a=1",
    }


def test_http_with_context_falls_back_without_context() -> None:
    client = http_with_context(FakeClient(), "https://follame.top/a/x.m3u8", None)
    assert client.headers == {"referer": "https://follame.top/"}
