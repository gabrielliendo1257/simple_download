from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_CONFIG_DIR = Path.home() / ".config" / "simple-downloader"
_CONFIG_FILE = _CONFIG_DIR / "config.json"


def catalog_db_path() -> Path:
    """Path del catálogo de jobs (SQLite) en el directorio de config."""
    return _CONFIG_DIR / "jobs.db"


@dataclass(frozen=True)
class TelegramConfig:
    """Credenciales del cliente de Telegram (Telethon, cuenta de usuario).

    La sesión se guarda en `~/.config/simple-downloader/<session_name>.session`
    después del primer login (`simple-downloader --telegram-login`).
    """

    enabled: bool = False
    api_id: int | None = None
    api_hash: str | None = None
    session_name: str = "simple_downloader"

    def is_usable(self) -> bool:
        return self.enabled and self.api_id is not None and self.api_hash is not None


@dataclass(frozen=True)
class UserConfig:
    """Preferencias de usuario leídas de `~/.config/simple-downloader/config.json`.

    Define únicamente los *defaults*: cada descarga puede sobrescribirlos
    desde la TUI (modal de añadir).
    """

    directory: Path = Path("downloads")
    template: str | None = None
    overwrite: bool = False
    telegram: TelegramConfig = TelegramConfig()

    @classmethod
    def defaults(cls) -> "UserConfig":
        return cls()


def load_user_config(path: Path | None = None) -> UserConfig:
    """Carga la config del usuario; crea el archivo con defaults si no existe.

    Un archivo corrupto o con claves inválidas no rompe la app: se vuelve a
    defaults (el archivo se conserva para que el usuario pueda corregirlo).
    """
    config_path = path or _CONFIG_FILE
    if not config_path.exists():
        _write_defaults(config_path)
        return UserConfig.defaults()

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return UserConfig.defaults()

    try:
        directory = Path(data.get("directory", "downloads")).expanduser()
        template = data.get("template")
        overwrite = bool(data.get("overwrite", False))
        telegram = _parse_telegram(data.get("telegram"))
    except (TypeError, AttributeError):
        return UserConfig.defaults()

    return UserConfig(
        directory=directory,
        template=template if isinstance(template, str) else None,
        overwrite=overwrite,
        telegram=telegram,
    )


def _parse_telegram(raw: object) -> TelegramConfig:
    if not isinstance(raw, dict):
        return TelegramConfig()

    try:
        api_id = raw.get("api_id")
        return TelegramConfig(
            enabled=bool(raw.get("enabled", False)),
            api_id=int(api_id) if api_id is not None else None,
            api_hash=(
                raw.get("api_hash") if isinstance(raw.get("api_hash"), str) else None
            ),
            session_name=(
                raw.get("session_name")
                if isinstance(raw.get("session_name"), str)
                else "simple_downloader"
            ),
        )
    except (TypeError, ValueError):
        return TelegramConfig()


def _write_defaults(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "directory": "downloads",
                    "template": None,
                    "overwrite": False,
                    "telegram": {
                        "enabled": False,
                        "api_id": None,
                        "api_hash": None,
                        "session_name": "simple_downloader",
                    },
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # sin permisos: la app funciona con defaults en memoria
