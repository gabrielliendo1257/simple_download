from __future__ import annotations

from simple_downloader.sources import YtDlpSource
from simple_downloader.ui.widgets import _pairs_to_headers, parse_headers


def test_parse_headers_kv_lines() -> None:
    assert parse_headers(
        "User-Agent: Mozilla/5.0\nAccept-Language: en-US,en;q=0.9"
    ) == {
        "user-agent": "Mozilla/5.0",
        "accept-language": "en-US,en;q=0.9",
    }


def test_parse_headers_curl_block() -> None:
    text = "-H 'User-Agent: Mozilla/5.0' -H \"Accept-Language: en-US,en;q=0.9\""
    assert parse_headers(text) == {
        "user-agent": "Mozilla/5.0",
        "accept-language": "en-US,en;q=0.9",
    }


def test_parse_headers_mixed_and_garbage() -> None:
    text = (
        "curl -H 'Referer: https://x/' -H 'Cookie: a=1'\n"
        "# comentario\n"
        "accept-language: es\n"
        "sin_dos_puntos_ignorada"
    )
    assert parse_headers(text) == {
        "referer": "https://x/",
        "cookie": "a=1",
        "accept-language": "es",
    }


def test_parse_headers_empty() -> None:
    assert parse_headers("") == {}
    assert parse_headers("  \n  \n") == {}


def test_pairs_to_headers_normalizes_and_skips_empty() -> None:
    pairs = [
        ("User-Agent", "Mozilla/5.0"),
        (" Accept-Language ", "en-US,en;q=0.9"),
        ("  ", "valor sin clave"),
        ("clave sin valor", "   "),
        ("", ""),
    ]
    assert _pairs_to_headers(pairs) == {
        "user-agent": "Mozilla/5.0",
        "accept-language": "en-US,en;q=0.9",
    }


def test_pairs_to_headers_empty_input() -> None:
    assert _pairs_to_headers([]) == {}


class FakeExecutor:
    def __init__(self) -> None:
        self.args: list[str] | None = None

    async def start(self, request) -> None:
        self.args = request.args


def _source() -> tuple[YtDlpSource, FakeExecutor]:
    from simple_downloader.executor import Executable, ExecutableStatus

    executor = FakeExecutor()
    executable = Executable(
        status=ExecutableStatus.ACTIVE, name="yt-dlp", path="/usr/bin/yt-dlp"
    )
    return YtDlpSource(executable=executable, executor=executor), executor


def test_ytdlp_download_adds_headers() -> None:
    source, executor = _source()

    import asyncio

    asyncio.run(
        source.download(
            "https://x/video", headers={"referer": "https://x/", "cookie": "a=1"}
        )
    )

    assert "--add-header" in (executor.args or [])
    assert "referer: https://x/" in (executor.args or [])
    assert "cookie: a=1" in (executor.args or [])


def test_ytdlp_download_without_headers_omits_flag() -> None:
    source, executor = _source()

    import asyncio

    asyncio.run(source.download("https://x/video"))

    assert "--add-header" not in (executor.args or [])


def test_ytdlp_download_adds_cookies_path() -> None:
    source, executor = _source()

    import asyncio

    asyncio.run(source.download("https://x/video", cookies_path="/tmp/cookies.txt"))

    args = executor.args or []
    assert "--cookies" in args
    assert args[args.index("--cookies") + 1] == "/tmp/cookies.txt"


def test_ytdlp_download_adds_cookies_from_browser() -> None:
    source, executor = _source()

    import asyncio

    asyncio.run(
        source.download("https://x/video", cookies_from_browser="firefox")
    )

    args = executor.args or []
    assert "--cookies-from-browser" in args
    assert args[args.index("--cookies-from-browser") + 1] == "firefox"


def test_ytdlp_download_omits_cookie_flags_when_absent() -> None:
    source, executor = _source()

    import asyncio

    asyncio.run(source.download("https://x/video"))

    args = executor.args or []
    assert "--cookies" not in args
    assert "--cookies-from-browser" not in args
