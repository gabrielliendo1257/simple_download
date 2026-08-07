from __future__ import annotations

import json
from pathlib import Path

from simple_downloader.infra.config import UserConfig, load_user_config


def test_defaults_when_file_missing(tmp_path: Path) -> None:
    config = load_user_config(tmp_path / "nope" / "config.json")

    assert config == UserConfig.defaults()
    assert config.directory == Path("downloads")


def test_creates_default_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    load_user_config(path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["directory"] == "downloads"
    assert data["template"] is None
    assert data["overwrite"] is False


def test_reads_user_values(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "directory": "~/media/descargas",
                "template": "{title}.{ext}",
                "overwrite": True,
            }
        ),
        encoding="utf-8",
    )

    config = load_user_config(path)

    assert config.directory == Path("~/media/descargas").expanduser()
    assert config.template == "{title}.{ext}"
    assert config.overwrite is True


def test_corrupt_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{esto no es json", encoding="utf-8")

    assert load_user_config(path) == UserConfig.defaults()


def test_wrong_types_fall_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"directory": 42, "template": 7}), encoding="utf-8")

    assert load_user_config(path) == UserConfig.defaults()


def test_ytdlp_cookies_from_browser_parsed(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"ytdlp": {"cookies_from_browser": "Firefox"}}),
        encoding="utf-8",
    )

    config = load_user_config(path)

    assert config.ytdlp.cookies_from_browser == "firefox"


def test_ytdlp_cookies_from_browser_rejects_unknown_browser(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"ytdlp": {"cookies_from_browser": "netscape"}}),
        encoding="utf-8",
    )

    config = load_user_config(path)

    assert config.ytdlp.cookies_from_browser is None


def test_ytdlp_cookies_defaults_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({}), encoding="utf-8")

    assert load_user_config(path).ytdlp == UserConfig.defaults().ytdlp
