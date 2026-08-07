from __future__ import annotations

import argparse
import asyncio
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="simple-downloader",
        description="Gestor de descargas con TUI y soporte de Telegram (Telethon).",
    )
    parser.add_argument(
        "--telegram-login",
        action="store_true",
        help="inicia sesión de Telegram y guarda la sesión (una sola vez)",
    )
    args = parser.parse_args()

    if args.telegram_login:
        asyncio.run(_run_telegram_login())
    else:
        from simple_downloader.ui.app import DownloadApp

        DownloadApp().run()


async def _run_telegram_login() -> None:
    from simple_downloader.engines.telegram import TelegramClientProvider
    from simple_downloader.infra.config import load_user_config

    config = load_user_config().telegram
    if not config.is_usable():
        print(
            "telegram no está configurado: edita la sección telegram de "
            "~/.config/simple-downloader/config.json (enabled, api_id, api_hash)",
            file=sys.stderr,
        )
        sys.exit(1)

    provider = TelegramClientProvider(config)
    me = await provider.get_me()
    name = me.first_name or me.username or str(me.id)
    print(f"Sesión de Telegram guardada para {name}.")
    await provider.disconnect()
