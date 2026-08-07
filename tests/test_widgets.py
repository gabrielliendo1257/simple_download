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


class _FakeQRProvider:
    async def qr_begin(self) -> str:
        return "https://t.me/login/abc"

    async def qr_wait(self, timeout: float = 25.0) -> bool:
        await asyncio.sleep(0.05)
        return False

    async def qr_refresh(self) -> str:
        return "https://t.me/login/abc2"


async def test_telegram_login_modal_closes_on_escape_without_killing_app() -> None:
    from textual.app import App
    from textual.screen import ModalScreen

    from simple_downloader.ui.widgets import TelegramLoginModal

    class Host(App[None]):
        def __init__(self) -> None:
            super().__init__()
            self.modal: ModalScreen[bool] | None = None

        async def on_mount(self) -> None:
            self.modal = TelegramLoginModal(_FakeQRProvider())
            await self.push_screen(self.modal)

    app = Host()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("escape")
        await pilot.pause(0.2)

        # El modal cerró y la app sigue viva (regresión: el modal pisaba
        # `_task` de Textual y el cierre mataba la app).
        assert app.screen is app.screen_stack[0]
        assert app.is_running


from textual.app import App
from textual.screen import ModalScreen

from simple_downloader.ui.widgets import TelegramLoginModal


class _ManualProvider:
    def __init__(self) -> None:
        self.sent_codes: list[str] = []
        self.sign_ins: list[tuple[str, str]] = []
        self.passwords: list[str] = []

    async def qr_begin(self) -> str:
        return "https://t.me/login/abc"

    async def qr_wait(self, timeout: float = 25.0) -> bool:
        await asyncio.sleep(0.05)
        return False

    async def qr_refresh(self) -> str:
        return "https://t.me/login/abc2"

    async def send_code_request(self, phone: str) -> None:
        self.sent_codes.append(phone)

    async def sign_in(self, phone: str, code: str):
        from simple_downloader.engines.telegram import (
            TelegramLoginNeedsPasswordError,
        )

        self.sign_ins.append((phone, code))
        raise TelegramLoginNeedsPasswordError()

    async def sign_in_password(self, password: str) -> None:
        self.passwords.append(password)


class _ManualHost(App[None]):
    def __init__(self, provider: object) -> None:
        super().__init__()
        self.modal: ModalScreen[bool] | None = None
        self.modal_result: asyncio.Future[bool] | None = None
        self.provider = provider

    async def on_mount(self) -> None:
        self.modal = TelegramLoginModal(self.provider)  # type: ignore[arg-type]
        self.push_screen(self.modal, callback=self._on_modal_result)

    def _on_modal_result(self, result: bool | None) -> None:
        self.modal_result = result


async def test_telegram_login_modal_manual_flow() -> None:
    provider = _ManualProvider()
    app = _ManualHost(provider)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.2)
        await pilot.press("m")
        await pilot.pause(0.1)

        phone = app.modal.query_one("#lg-phone")
        code = app.modal.query_one("#lg-code")
        password = app.modal.query_one("#lg-password")
        assert phone.display is not False
        assert code.display is False
        assert password.display is False

        phone.focus()
        await pilot.press(*"+5491100000000")
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert provider.sent_codes == ["+5491100000000"]
        assert code.display is not False

        code.focus()
        await pilot.press(*"12345")
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert provider.sign_ins == [("+5491100000000", "12345")]
        assert password.display is not False

        password.focus()
        await pilot.press(*"mi-clave")
        await pilot.press("enter")
        await pilot.pause(0.2)

        assert provider.passwords == ["mi-clave"]
        assert app.modal_result is True
        assert app.screen is app.screen_stack[0]
        assert app.is_running
