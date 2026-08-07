from __future__ import annotations

import argparse
import asyncio
import sys

from simple_downloader.infra.qr import qr_ascii

_QR_WAIT_SECONDS = 25.0  # una sesión de QR dura ~30s


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="simple-downloader",
        description="Gestor de descargas con TUI y soporte de Telegram (Telethon).",
    )
    parser.add_argument(
        "--telegram-login",
        action="store_true",
        help="inicia sesión de Telegram con un QR y guarda la sesión",
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
    try:
        url = await provider.qr_begin()
    except Exception as exc:
        print(f"no se pudo iniciar el QR: {exc}", file=sys.stderr)
        sys.exit(1)

    print("Escaneá el QR con la app de Telegram para iniciar sesión (Ctrl+C cancela):")
    print(qr_ascii(url))
    while True:
        try:
            ok = await provider.qr_wait(_QR_WAIT_SECONDS)
        except KeyboardInterrupt:
            await provider.disconnect()
            sys.exit(1)
        if ok:
            break
        url = await provider.qr_refresh()
        print("El QR expiró; escaneá el nuevo:")
        print(qr_ascii(url))

    me = await provider.get_me()
    name = me.first_name or me.username or str(me.id)
    print(f"Sesión de Telegram guardada para {name}.")
    await provider.disconnect()
