from __future__ import annotations

import asyncio

from simple_downloader.ui.widgets import PathSuggester


async def test_suggester_completes_directory(tmp_path) -> None:
    (tmp_path / "downloads").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "diario.txt").write_text("x")

    base = str(tmp_path)
    sug = await PathSuggester().get_suggestion(f"{base}/do")

    # Orden alfabético, dirs primero: docs < downloads.
    assert sug == f"{base}/docs/"


async def test_suggester_prefers_dirs_over_files(tmp_path) -> None:
    (tmp_path / "media").mkdir()
    (tmp_path / "media.txt").write_text("x")

    base = str(tmp_path)
    sug = await PathSuggester().get_suggestion(f"{base}/m")

    assert sug == f"{base}/media/"


async def test_suggester_keeps_tilde(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "downloads").mkdir()

    sug = await PathSuggester().get_suggestion("~/do")

    assert sug == "~/downloads/"


async def test_suggester_returns_none_without_match(tmp_path) -> None:
    sug = await PathSuggester().get_suggestion(f"{tmp_path}/zzz")

    assert sug is None


async def test_suggester_returns_none_on_empty_value(tmp_path) -> None:
    assert await PathSuggester().get_suggestion("") is None
