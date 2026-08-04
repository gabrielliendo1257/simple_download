from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_CONFIG_DIR = Path.home() / ".config" / "simple-downloader"
_CONFIG_FILE = _CONFIG_DIR / "config.json"


@dataclass(frozen=True)
class UserConfig:
    """Preferencias de usuario leídas de `~/.config/simple-downloader/config.json`.

    Define únicamente los *defaults*: cada descarga puede sobrescribirlos
    desde la TUI (modal de añadir).
    """

    directory: Path = Path("downloads")
    template: str | None = None
    overwrite: bool = False

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
    except (TypeError, AttributeError):
        return UserConfig.defaults()

    return UserConfig(
        directory=directory,
        template=template if isinstance(template, str) else None,
        overwrite=overwrite,
    )


def _write_defaults(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "directory": "downloads",
                    "template": None,
                    "overwrite": False,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass  # sin permisos: la app funciona con defaults en memoria
