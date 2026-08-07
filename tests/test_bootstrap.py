import json
from pathlib import Path

from simple_downloader.app.bootstrap import Backend
from simple_downloader.engines.ytdlp import YtDlpEngine
from simple_downloader.infra.config import UserConfig


def _write_config(path: Path, cookies: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "directory": "downloads",
                "ytdlp": {"cookies_from_browser": cookies},
            }
        ),
        encoding="utf-8",
    )


def _backend_with_engine(config_path: Path) -> Backend:
    return Backend(
        event_bus=object(),  # type: ignore[arg-type]
        manager=object(),  # type: ignore[arg-type]
        scheduler=object(),  # type: ignore[arg-type]
        source_provider=object(),  # type: ignore[arg-type]
        engine_registry=object(),  # type: ignore[arg-type]
        config=UserConfig.defaults(),
        ytdlp_engine=YtDlpEngine(source_provider=object()),  # type: ignore[arg-type]
        config_path=config_path,
    )


def test_reload_config_reads_disk_changes(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, cookies=None)
    backend = _backend_with_engine(config_path)

    _write_config(config_path, cookies="firefox")

    fresh = backend.reload_config()

    assert fresh.ytdlp.cookies_from_browser == "firefox"
    assert backend.config is fresh


def test_reload_config_propagates_cookies_to_ytdlp_engine(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, cookies="chrome")
    backend = _backend_with_engine(config_path)

    backend.reload_config()

    assert backend.ytdlp_engine is not None
    assert backend.ytdlp_engine.cookies_from_browser == "chrome"


def test_reload_config_keeps_defaults_when_file_removed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_config(config_path, cookies="edge")
    backend = _backend_with_engine(config_path)

    config_path.unlink()

    fresh = backend.reload_config()

    assert fresh.ytdlp.cookies_from_browser is None
    assert backend.ytdlp_engine is not None
    assert backend.ytdlp_engine.cookies_from_browser is None
