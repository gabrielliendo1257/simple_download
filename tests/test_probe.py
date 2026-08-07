import asyncio

import pytest

from simple_downloader.domain.models import DownloadOutput, DownloadRequest
from simple_downloader.engines.hls import HlsEngine, SegmentFormat, sniff_segment
from simple_downloader.engines.hls.probe import probe_segment


class ProbeClient:
    def __init__(self, first: bytes, rest: bytes | None = None) -> None:
        self.first = first
        self.rest = rest if rest is not None else first
        self.range_calls: list[tuple[str, int, int]] = []
        self.full_calls = 0

    async def get_range(self, url: str, start: int, end: int) -> bytes:
        self.range_calls.append((url, start, end))
        return self.first

    async def get(self, url: str) -> bytes:
        self.full_calls += 1
        return self.rest


def test_sniff_detects_png_wrapped() -> None:
    assert sniff_segment(b"\x89PNG\r\n\x1a\n" + b"junk") is SegmentFormat.PNG_WRAPPED


def test_sniff_detects_fmp4() -> None:
    assert sniff_segment(b"\x00\x00\x00\x18ftypisom") is SegmentFormat.FMP4
    assert (
        sniff_segment(b"\x00\x00\x00\x10moof\x00\x00\x00\x10mfhd") is SegmentFormat.FMP4
    )


def test_sniff_detects_emsg_prefixed_fmp4() -> None:
    # Los streams de Apple encabezan los .m4s con una caja emsg (eventos)
    # antes del primer moof.
    assert (
        sniff_segment(b"\x00\x00\x00\x8demsg\x01\x00\x00\x00\x00\x01_\x90")
        is SegmentFormat.FMP4
    )
    assert sniff_segment(b"\x00\x00\x00\x10prft") is SegmentFormat.FMP4
    assert sniff_segment(b"\x00\x00\x00\x18pssh") is SegmentFormat.FMP4


def test_sniff_detects_ts() -> None:
    raw = bytes([0x47]) * 377
    assert sniff_segment(raw) is SegmentFormat.TS


def test_sniff_unknown() -> None:
    assert sniff_segment(b"garbage!!!") is SegmentFormat.UNKNOWN


def test_probe_uses_range_when_available() -> None:
    client = ProbeClient(b"\x89PNG\r\n\x1a\n" + b"x")
    result = asyncio.run(probe_segment(client, "https://s/seg0.ts"))

    assert result is SegmentFormat.PNG_WRAPPED
    assert client.range_calls == [("https://s/seg0.ts", 0, 8191)]
    assert client.full_calls == 0


def test_probe_falls_back_to_full_get() -> None:
    class NoRangeClient:
        async def get(self, url: str) -> bytes:
            return bytes([0x47]) * 377

    result = asyncio.run(probe_segment(NoRangeClient(), "https://s/seg0.ts"))
    assert result is SegmentFormat.TS


def _engine_with(segment_bytes: bytes) -> HlsEngine:
    class PlaylistClient:
        async def get_range(self, url, start, end):
            return segment_bytes

        async def get(self, url):
            return b"#EXTM3U\n#EXTINF:4.0,\nseg0.ts\n#EXT-X-ENDLIST\n"

    return HlsEngine(PlaylistClient())


def _output(tmp_path, name: str) -> DownloadOutput:
    return DownloadOutput(directory=tmp_path, filename=name)


def test_hls_engine_downloads_standard_ts_locally(tmp_path) -> None:
    engine = _engine_with(bytes([0x47]) * 377)
    request = DownloadRequest(
        url="https://cdn.example.com/media.m3u8",
        output=_output(tmp_path, "out.ts"),
    )

    task = asyncio.run(engine.create_task(request))

    assert task.out_file == tmp_path / "out.ts"
    assert task.segments[0].uri == "https://cdn.example.com/seg0.ts"


def test_hls_engine_handles_fmp4_locally(tmp_path) -> None:
    engine = _engine_with(b"\x00\x00\x00\x18ftypisom")
    request = DownloadRequest(
        url="https://cdn.example.com/media.m3u8",
        output=_output(tmp_path, "out.mp4"),
    )

    task = asyncio.run(engine.create_task(request))

    assert task.out_file == tmp_path / "out.mp4"


def test_hls_engine_handles_png_wrapped_locally(tmp_path) -> None:
    engine = _engine_with(b"\x89PNG\r\n\x1a\n" + b"junk")
    request = DownloadRequest(
        url="https://cdn.example.com/media.m3u8",
        output=_output(tmp_path, "out.mp4"),
    )

    task = asyncio.run(engine.create_task(request))

    assert task.out_file == tmp_path / "out.mp4"


def test_hls_engine_raises_on_unknown_segment() -> None:
    engine = _engine_with(b"garbage!!!")
    request = DownloadRequest(url="https://cdn.example.com/media.m3u8")

    with pytest.raises(ValueError, match="no se reconoce"):
        asyncio.run(engine.create_task(request))
