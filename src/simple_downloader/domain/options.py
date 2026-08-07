from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class FieldKind(Enum):
    """Tipo de input que el modal debe renderizar para un campo."""

    TEXT = auto()  # Input de una línea
    PATH = auto()  # Input de rutas con autocompletado (PathInput)
    NUMBER = auto()  # Input numérico (validado al confirmar)
    HEADERS = auto()  # Filas clave/valor dinámicas (modal de headers)
    CHOICE = auto()  # Select desplegable (opciones dinámicas de la URL)


@dataclass(frozen=True)
class FieldOption:
    """Opción de un campo CHOICE: lo que se muestra vs. lo que se envía.

    Ej. resolución: label "1080p (mp4)" -> value "137" (format_id de yt-dlp).
    """

    label: str
    value: str


@dataclass(frozen=True)
class ModalField:
    """Especificación declarativa de un campo del modal de añadir.

    Cada engine declara los campos que le sirven (`Engine.modal_fields`)
    y el modal los renderiza genéricamente: la TUI no sabe qué engine
    va a resolver la URL ni qué significan los campos.

    La clave (`key`) pertenece a un vocabulario compartido que la app
    traduce a `DownloadContext` (`context_from_fields`).

    Los campos CHOICE llevan las opciones vacías en la spec estática;
    la app las rellena en runtime con `Engine.modal_options(url)`
    (especificación dinámica según la URL resuelta).
    """

    key: str
    label: str
    placeholder: str = ""
    kind: FieldKind = FieldKind.TEXT
    options: tuple[FieldOption, ...] = ()


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

FORMAT_FIELD = ModalField(
    "format_id",
    "Resolución",
    "Elegí una resolución",
    FieldKind.CHOICE,
)
