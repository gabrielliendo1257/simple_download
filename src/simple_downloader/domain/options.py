from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class FieldKind(Enum):
    """Tipo de input que el modal debe renderizar para un campo."""

    TEXT = auto()  # Input de una línea
    PATH = auto()  # Input de rutas con autocompletado (PathInput)
    NUMBER = auto()  # Input numérico (validado al confirmar)
    HEADERS = auto()  # Filas clave/valor dinámicas (modal de headers)


@dataclass(frozen=True)
class ModalField:
    """Especificación declarativa de un campo del modal de añadir.

    Cada engine declara los campos que le sirven (`Engine.modal_fields`)
    y el modal los renderiza genéricamente: la TUI no sabe qué engine
    va a resolver la URL ni qué significan los campos.

    La clave (`key`) pertenece a un vocabulario compartido que la app
    traduce a `DownloadContext` (`context_from_fields`).
    """

    key: str
    label: str
    placeholder: str = ""
    kind: FieldKind = FieldKind.TEXT


# Vocabulario compartido entre engines (keys usadas por context_from_fields).

HEADERS_FIELD = ModalField(
    "headers", "Headers (opcional)", "", FieldKind.HEADERS
)

COOKIES_FIELD = ModalField(
    "cookies_path",
    "Cookies de yt-dlp (.txt, opcional)",
    "",
    FieldKind.PATH,
)

USER_AGENT_FIELD = ModalField(
    "user_agent", "User-Agent (opcional)", "", FieldKind.TEXT
)

PARALLEL_SEGMENTS_FIELD = ModalField(
    "max_parallel_segments",
    "Segmentos en paralelo",
    "6",
    FieldKind.NUMBER,
)
