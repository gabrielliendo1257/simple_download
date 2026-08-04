import pytest

from simple_downloader.engines.hls.fetch import unwrap_ts

TS = bytes([0x47]) * 188 * 10
PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00"


def _png_wrapped(payload: bytes, junk: bytes = b"") -> bytes:
    return PNG_HEADER + junk + b"IEND\xaeB`\x82" + payload


def test_unwrap_png_bare() -> None:
    assert unwrap_ts(_make_wrapped_plain()) == TS


def test_unwrap_with_random_junk_before_start() -> None:
    wrapped = PNG_HEADER + bytes(range(0, 256)) * 3 + b"IEND\xaeB`\x82" + TS
    assert unwrap_ts(wrapped) == TS


def test_unwrap_returns_offset_payload_when_sync_matches() -> None:
    payload = b"\x00" * 30 + TS
    assert unwrap_ts(_make_wrapped(payload)) == TS


def test_unwrap_raises_when_no_png_marker() -> None:
    with pytest.raises(ValueError, match="IEND"):
        unwrap_ts(b"\x00" * 100)


def test_unwrap_raises_when_ts_sync_absent() -> None:
    payload = b"\x01" * (188 * 8)
    with pytest.raises(ValueError, match="0x47"):
        unwrap_ts(_make_wrapped(payload))


def test_unwrap_short_payload_raises() -> None:
    with pytest.raises(ValueError, match="0x47"):
        unwrap_ts(_make_wrapped(b"\x01\x02"), sync_samples=1)


def _make_wrapped(payload: bytes) -> bytes:
    return PNG_HEADER + b"IEND\xaeB`\x82" + payload


def _make_wrapped_plain() -> bytes:
    return PNG_HEADER + b"IEND\xaeB`\x82" + TS