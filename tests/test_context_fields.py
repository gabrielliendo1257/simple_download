from __future__ import annotations

import pytest

from simple_downloader.ui.app import context_from_fields, format_id_from_fields


def test_context_from_fields_none_when_empty() -> None:
    assert context_from_fields(None) is None
    assert context_from_fields({}) is None


def test_context_from_fields_parses_headers() -> None:
    ctx = context_from_fields(
        {"headers": "User-Agent: Mozilla\nReferer: https://x/"}
    )
    assert ctx is not None
    assert ctx.headers == {"user-agent": "Mozilla", "referer": "https://x/"}


def test_context_from_fields_maps_options() -> None:
    ctx = context_from_fields(
        {
            "headers": "",
            "cookies_path": "/tmp/cookies.txt",
            "user_agent": "my-agent",
            "max_parallel_segments": "4",
        }
    )
    assert ctx is not None
    assert ctx.headers == {}
    assert ctx.cookies_path == "/tmp/cookies.txt"
    assert ctx.user_agent == "my-agent"
    assert ctx.max_parallel_segments == 4


def test_context_from_fields_default_parallel() -> None:
    ctx = context_from_fields({"user_agent": "x"})
    assert ctx is not None
    assert ctx.max_parallel_segments == 6


def test_context_from_fields_rejects_bad_parallel() -> None:
    with pytest.raises(ValueError, match="segmentos"):
        context_from_fields({"max_parallel_segments": "abc"})
    with pytest.raises(ValueError, match="segmentos"):
        context_from_fields({"max_parallel_segments": "0"})


def test_format_id_from_fields() -> None:
    assert format_id_from_fields(None) is None
    assert format_id_from_fields({}) is None
    assert format_id_from_fields({"format_id": "137"}) == "137"
    assert format_id_from_fields({"format_id": "best"}) == "best"
    assert format_id_from_fields({"user_agent": "x"}) is None
