from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Button, Input, Label, ListItem, Static

from simple_downloader.domain.models import DownloadOutput
from simple_downloader.domain.options import FieldKind, ModalField
from simple_downloader.infra.qr import qr_ascii

# ── Paleta semántica ──────────────────────────────────────────────────────
# Azul → información, Verde → éxito, Amarillo → advertencia, Rojo → error,
# Morado → acciones, Cian → selección/actividad, Gris → texto secundario.

ACCENT = "#39c5cf"  # selección / actividad
INFO = "#58a6ff"  # información
SUCCESS = "#3fb950"  # éxito
WARNING = "#d29922"  # advertencia
ERROR = "#f85149"  # error
ACTION = "#bc8cff"  # acciones (teclas del footer)
TEXT = "#e6edf3"  # texto principal
MUTED = "#8b949e"  # texto secundario
DIM = "#6e7681"  # texto terciario
SURFACE = "#161b22"  # superficies (input, modales, stats)
BORDER = "#30363d"  # bordes
SEPARATOR = "#21262d"  # separadores


class DownloadStatus(Enum):
    QUEUED = auto()
    DOWNLOADING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    ERROR = auto()
    CANCELLED = auto()


_STATUS_META: dict[DownloadStatus, tuple[str, str, str]] = {
    DownloadStatus.QUEUED: ("⏳", "En cola", MUTED),
    DownloadStatus.DOWNLOADING: ("↓", "Descargando", ACCENT),
    DownloadStatus.PAUSED: ("⏸", "Pausada", WARNING),
    DownloadStatus.COMPLETED: ("✓", "Completada", SUCCESS),
    DownloadStatus.ERROR: ("✗", "Error", ERROR),
    DownloadStatus.CANCELLED: ("✕", "Cancelada", DIM),
}


@dataclass
class Download:
    """Modelo de vista: lo que la UI necesita para renderizar un job."""

    filename: str
    download_id: str
    total_bytes: int
    downloaded_bytes: int = 0
    speed_bps: float = 0.0
    segments_done: int | None = None
    segments_total: int | None = None
    status: DownloadStatus = DownloadStatus.QUEUED
    eta_sec: float | None = None
    error_message: str | None = None
    url: str | None = None
    engine: str | None = None
    destination: str | None = None
    notice: str | None = None

    @property
    def progress(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return min(self.downloaded_bytes / self.total_bytes, 1.0)

    @property
    def progress_pct(self) -> str:
        if self.total_bytes <= 0:
            return "—"
        return f"{self.progress * 100:.1f}%"

    @staticmethod
    def size_str(bytes_: int) -> str:
        if bytes_ is None:
            return "--"
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if bytes_ < 1024:
                return f"{bytes_:.1f} {unit}"
            bytes_ = int(bytes_ / 1024)
        return f"{bytes_:.1f} PB"

    @property
    def downloaded_str(self) -> str:
        return self.size_str(self.downloaded_bytes)

    @property
    def total_str(self) -> str:
        return self.size_str(self.total_bytes)

    @property
    def speed_str(self) -> str:
        return f"{self.size_str(int(self.speed_bps))}/s"

    @property
    def eta_str(self) -> str:
        if self.eta_sec is None:
            return "—"
        m, s = divmod(int(self.eta_sec), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}h {m:02d}m {s:02d}s"
        if m:
            return f"{m}m {s:02d}s"
        return f"{s}s"


def _elide(text: str, width: int, *, tail_ratio: float = 0.6) -> str:
    """Recorta con elipsis central, conservando el final (nombre de archivo)."""
    if width <= 1:
        return ""
    if len(text) <= width:
        return text
    head = int((width - 1) * (1 - tail_ratio))
    return f"{text[:max(head, 0)]}…{text[-(width - 1 - head):]}"


class DownloadItem(ListItem):
    """Fila de descarga: estado, nombre y barra de progreso en dos líneas."""

    download: reactive[Download | None] = reactive(None, always_update=True)

    def __init__(
        self, download: Download | None = None, *, id: str | None = None
    ) -> None:
        super().__init__(id=id)
        self._row = Static("", id="row")
        self.download = download

    def compose(self) -> ComposeResult:
        yield from (self._row,)

    def update_download(self, dl: Download | None) -> None:
        self.download = dl

    def watch_download(self, dl: Download | None) -> None:
        self._row.update(self._rows(dl))

    def _rows(self, dl: Download | None) -> Text:
        if dl is None:
            return Text("")

        width = max(self._row.content_size.width, 40)
        selected = self.has_class("-highlight")
        icon, label, color = _STATUS_META[dl.status]
        marker = "▎" if selected else " "

        title = _elide(dl.filename, max(width - 6 - len(label), 8))
        line1 = Text.assemble(
            (marker, f"bold {ACCENT}" if selected else "dim"),
            (f" {icon} ", f"bold {color}"),
            (title, "bold" if selected else ""),
            (" " * max(1, width - 5 - len(title) - len(label)), ""),
            (label, color),
        )

        line2 = self._meta_line(width, dl, marker)

        lines = [line1, line2]
        if dl.notice:
            notice = _elide(dl.notice, min(width - 5, 80))
            lines.append(
                Text.assemble(
                    (f"{marker}   ", "dim"),
                    ("⚠ ", f"bold {WARNING}"),
                    (notice, WARNING),
                )
            )
        return Text("\n").join(lines)

    def _meta_line(self, width: int, dl: Download, marker: str) -> Text:
        prefix = f"{marker}    "  # alinea con el título (col 5)
        if dl.status is DownloadStatus.DOWNLOADING:
            return self._progress_line(width, dl, ACCENT, prefix)
        if dl.status is DownloadStatus.PAUSED:
            return self._progress_line(width, dl, MUTED, prefix)

        if dl.status is DownloadStatus.COMPLETED:
            detail = f" {dl.total_str}  ·  100%"
            return Text.assemble((prefix, "dim"), (detail, SUCCESS))

        if dl.status is DownloadStatus.ERROR and dl.error_message:
            message = _elide(dl.error_message, min(width - 5, 60))
            return Text.assemble((prefix, "dim"), (message, ERROR))

        if dl.status is DownloadStatus.QUEUED:
            return Text.assemble((prefix, "dim"), ("en cola para descargar…", DIM))

        return Text.assemble((prefix, "dim"), ("descarga cancelada", DIM))

    def _progress_line(
        self, width: int, dl: Download, bar_color: str, prefix: str
    ) -> Text:
        total = dl.total_bytes
        downloaded = dl.downloaded_str
        size = f" {downloaded} / {dl.total_str}" if total else f" {downloaded}"

        extra: list[str] = []
        if dl.segments_done is not None and dl.segments_total:
            extra.append(f" seg {dl.segments_done}/{dl.segments_total}")
        if dl.speed_bps:
            extra.append(f" {dl.speed_str}")
        if dl.speed_bps and width >= 96:
            extra.append(f" ETA {dl.eta_str}")
        stats = size + "".join(f"│{part}" for part in extra)

        pct = f" {dl.progress_pct:>6}" if total else "     —"
        bar_area = width - len(prefix) - len(pct) - len(stats) - 1

        if bar_area >= 6:
            filled = round(bar_area * dl.progress)
            bar = "█" * filled + "░" * (bar_area - filled)
            return Text.assemble(
                (prefix, "dim"),
                (bar, bar_color),
                (pct, "bold"),
                (stats, "dim"),
            )

        return Text.assemble((prefix, "dim"), (pct, "bold"), (stats, "dim"))


class StatsBar(Static):
    """Barra de resumen: contadores por estado y velocidad agregada."""

    def update_stats(
        self,
        *,
        total: int,
        active: int,
        queued: int,
        completed: int,
        failed: int,
        speed: float,
    ) -> None:
        t = Text()
        t.append(f"  {total} descargas", "bold")
        t.append("   │  ", DIM)
        t.append(f"● {active} activas", ACCENT)
        t.append("   ", DIM)
        t.append(f"⏳ {queued} en cola", MUTED)
        t.append("   ", DIM)
        t.append(f"✓ {completed} completadas", SUCCESS)
        t.append("   ", DIM)
        if failed:
            t.append(f"✗ {failed} con error", ERROR)
        else:
            t.append(f"✗ {failed} con error", DIM)
        t.append("   │  ", DIM)
        t.append(f"⚡ {Download.size_str(int(speed))}/s", f"bold {ACCENT}")
        self.update(t)


class DetailsModal(ModalScreen[None]):
    """Detalle de una descarga: URL, motor, tamaño, velocidad, destino…"""

    BINDINGS = [
        Binding("enter", "dismiss", "Cerrar"),
        Binding("escape", "dismiss", "Cerrar"),
    ]

    def __init__(self, dl: Download) -> None:
        super().__init__()
        self._dl = dl

    def compose(self) -> ComposeResult:
        with Container(id="details-card"):
            yield Static(
                Text.assemble((" Detalles de la descarga", "bold white")),
                id="details-title",
            )
            yield Static(self._details_text(self._dl), id="details-content")
            yield Static("Enter / Esc — cerrar", id="modal-hint")

    @staticmethod
    def _details_text(dl: Download) -> Text:
        icon, label, color = _STATUS_META[dl.status]
        rows = [
            ("Estado", Text.assemble((f" {icon} ", f"bold {color}"), (label, color))),
            ("Título", Text(dl.filename, style="bold")),
            ("URL", Text(dl.url or "—")),
            ("Motor", Text(dl.engine or "—", style=ACCENT)),
            ("Tamaño", Text(dl.total_str if dl.total_bytes else "—")),
            ("Descargado", Text(f"{dl.downloaded_str}  ({dl.progress_pct})")),
            (
                "Segmentos",
                Text(
                    f"{dl.segments_done} / {dl.segments_total}"
                    if dl.segments_total
                    else "—"
                ),
            ),
            ("Velocidad", Text(dl.speed_str if dl.speed_bps else "—")),
            ("ETA", Text(dl.eta_str)),
            ("Destino", Text(dl.destination or "—")),
        ]
        body = Text()
        for name, value in rows:
            body.append(f" {name:<11}", DIM)
            body.append_text(value)
            body.append("\n")
        if dl.error_message:
            body.append(" Error      ", DIM)
            body.append(dl.error_message, ERROR)
            body.append("\n")
        if dl.notice:
            body.append(" Aviso      ", DIM)
            body.append(dl.notice, WARNING)
            body.append("\n")
        return body


class ConfirmModal(ModalScreen[bool]):
    """Confirmación para acciones destructivas (cancelar descarga)."""

    BINDINGS = [
        Binding("y", "confirm", "Sí"),
        Binding("n", "dismiss", "No"),
        Binding("escape", "dismiss", "Cancelar"),
    ]

    def __init__(self, title: str, message: str) -> None:
        super().__init__()
        self._title = title
        self._message = message

    def action_confirm(self) -> None:
        self.dismiss(True)

    def compose(self) -> ComposeResult:
        with Container(id="confirm-card"):
            yield Static(
                Text.assemble(("⚠ ", f"bold {WARNING}"), (self._title, "bold white")),
                id="confirm-title",
            )
            yield Static(self._message, id="confirm-message")
            yield Static("[y] Sí    [n] No    [esc] Cancelar", id="modal-hint")


_CURL_H_HEADER = re.compile(r"""-H\s+(?:"([^"]*)"|'([^']*)'|([^\s]+))""")
_CURL_HEADER_FLAGS = frozenset({"-H", "--header"})


def parse_headers(text: str) -> dict[str, str]:
    """Convierte el campo de headers en dict normalizado a minúsculas.

    Acepta `Clave: Valor` por línea y bloques curl como
    `curl -H 'Accept-Language: en-US,en' -H "User-Agent: Firefox"`.
    Las líneas que no contienen `:` ni flags curl se ignoran.
    """
    headers: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "-H" in stripped or "--header" in stripped:
            for match in _CURL_H_HEADER.finditer(stripped):
                raw = match.group(1) or match.group(2) or match.group(3)
                _add_header(headers, raw)
        else:
            _add_header(headers, stripped)
    return headers


def _add_header(headers: dict[str, str], raw: str) -> None:
    key, sep, value = raw.partition(":")
    if sep and key.strip():
        headers[key.strip().lower()] = value.strip()


def _pairs_to_headers(pairs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Convierte pares (clave, valor) de las filas del modal en headers.

    Claves normalizadas a minúsculas; pares vacíos ignorados.
    """
    headers: dict[str, str] = {}
    for key, value in pairs:
        normalized = key.strip().lower()
        if normalized and value.strip():
            headers[normalized] = value.strip()
    return headers


@dataclass
class AddDownloadResult:
    output: DownloadOutput | None
    field_values: dict[str, str]


class TelegramLoginModal(ModalScreen[bool]):
    """Login de Telegram por QR en runtime.

    Muestra el QR como ASCII y lo refresca cuando expira (~25s) hasta
    que se escanea (vuelve `True`) o se cancela (`False`). La sesión
    queda guardada: no hace falta volver a loguearse.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
        Binding("c", "cancel", "Cancelar"),
    ]

    def __init__(self, provider) -> None:
        super().__init__()
        self._provider = provider
        self._task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        with Container(id="qr-card"):
            yield Static(
                Text.assemble(("🔐  Iniciar sesión en Telegram", "bold white")),
                id="qr-title",
            )
            yield Static("", id="qr-code")
            yield Static("", id="qr-hint")
            yield Static("[esc] Cancelar", id="modal-hint")

    def on_mount(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def on_unmount(self) -> None:
        if self._task is not None:
            self._task.cancel()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def _show_code(self, url: str) -> None:
        self.query_one("#qr-code", Static).update(qr_ascii(url))
        self.query_one("#qr-hint", Static).update(
            Text("Escaneá el QR con la app de Telegram para iniciar sesión", DIM)
        )

    def _fail(self, message: str) -> None:
        self.query_one("#qr-code", Static).update("")
        self.query_one("#qr-title", Static).update(
            Text.assemble(("✗  No se pudo iniciar sesión", "bold"), (f"\n{message}", ERROR))
        )

    async def _run(self) -> None:
        try:
            url = await self._provider.qr_begin()
        except Exception as exc:
            self._fail(str(exc))
            return
        self._show_code(url)
        while True:
            try:
                ok = await self._provider.qr_wait(25)
            except Exception as exc:
                self._fail(str(exc))
                return
            if ok:
                self.dismiss(True)
                return
            url = await self._provider.qr_refresh()
            self._show_code(url)


class PathSuggester(Suggester):
    """Completa rutas del filesystem: sugerencia fantasma estilo shell.

    Las sugerencias son relativas a lo escrito: `~/do` completa a
    `~/downloads/` (conservando `~`), y los directorios llevan `/`
    final para distinguirlos de los archivos.
    """

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None

        base = os.path.dirname(value)
        prefix = os.path.basename(value)
        list_base = os.path.expanduser(base) or "."

        try:
            entries = sorted(os.listdir(list_base))
        except OSError:
            return None

        matches = [entry for entry in entries if entry.startswith(prefix)]
        dirs = [
            entry
            for entry in matches
            if os.path.isdir(os.path.join(list_base, entry))
        ]
        files = [
            entry
            for entry in matches
            if not os.path.isdir(os.path.join(list_base, entry))
        ]

        best = (dirs + files)[0] if dirs or files else None
        if best is None:
            return None
        if best in dirs:
            best += "/"
        return os.path.join(base, best) if base else best


class PathInput(Input):
    """Input de rutas: Tab acepta la sugerencia como en una shell."""

    def accept_suggestion(self) -> bool:
        if self.cursor_at_end and self._suggestion:
            self.value = self._suggestion
            self.cursor_position = len(self.value)
            return True
        return False


class AddDownloadModal(ModalScreen[AddDownloadResult | None]):
    """Añade una descarga: nombre, carpeta y los campos del engine.

    El modal no sabe qué engine resuelve la URL: recibe su
    especificación (`ModalField`) y renderiza nombre + carpeta (fijos)
    más los campos declarados. El resultado es un dict genérico
    (`field_values`) que la app traduce a `DownloadContext`.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
        Binding("ctrl+enter", "confirm", "Añadir"),
    ]

    def __init__(
        self,
        url: str,
        *,
        default_name: str = "",
        directory: str = "downloads",
        fields: list[ModalField] | None = None,
    ) -> None:
        super().__init__()
        self._url = url
        self._default_name = default_name
        self._default_directory = directory
        self._fields = list(fields or [])
        self._row_count = 1  # la fila 0 de headers se crea en compose

    def compose(self) -> ComposeResult:
        with Container(id="add-card"):
            yield Static(
                Text.assemble((" Añadir descarga", "bold white")),
                id="add-title",
            )
            yield Static(Text(self._url, style=DIM, no_wrap=False), id="add-url")
            yield Input(
                value=self._default_name,
                placeholder="Nombre del archivo (sin extensión)",
                id="add-name",
            )
            yield PathInput(
                value=self._default_directory,
                placeholder="Carpeta de destino",
                id="add-folder",
                suggester=PathSuggester(),
            )
            yield from self._render_fields()
            with Container(id="add-actions"):
                yield Button("Añadir", id="add-submit")
                yield Button("Cancelar", id="add-cancel")

    def _render_fields(self) -> ComposeResult:
        for spec in self._fields:
            if spec.kind is FieldKind.HEADERS:
                yield Label(spec.label, classes="field-label")
                with VerticalScroll(id="headers-fields"):
                    yield self._make_header_row(0)
            elif spec.kind is FieldKind.PATH:
                yield Label(spec.label, classes="field-label")
                yield PathInput(
                    placeholder=spec.placeholder,
                    id=f"field-{spec.key}",
                    classes="field-input",
                    suggester=PathSuggester(),
                )
            else:
                yield Label(spec.label, classes="field-label")
                yield Input(
                    placeholder=spec.placeholder,
                    id=f"field-{spec.key}",
                    classes="field-input",
                )

    def on_mount(self) -> None:
        # Cursor fijo sin parpadeo: en tmux el blink de Textual no
        # refresca bien y el cursor parece inexistente.
        for field in self.query(Input):
            field.cursor_blink = False
        self.query_one("#add-name", Input).focus()

    def on_key(self, event: events.Key) -> None:
        """Tab en una ruta con sugerencia la acepta (como una shell);
        sin sugerencia, Tab sigue ciclando el foco como siempre."""
        if event.key == "tab":
            focused = self.focused
            if isinstance(focused, PathInput) and focused.accept_suggestion():
                event.stop()

    def apply_external_title(self, title: str) -> None:
        """Sobrescribe el nombre si el usuario aún no lo editó."""
        name = self.query_one("#add-name", Input)
        if name.value == self._default_name and title:
            name.value = title
            self._default_name = title

    async def on_input_changed(self, event: Input.Changed) -> None:
        """Si la última fila de headers tiene contenido, crea la siguiente."""
        if self._row_index(event.input.id) is None:
            return
        if self._last_row_has_text():
            await self._add_header_row()

    def _field_ids(self) -> list[str]:
        """Ids de los inputs en orden de foco: primero la fila 0 de
        headers (si el engine la pidió), luego los campos simples."""
        ids: list[str] = []
        for spec in self._fields:
            if spec.kind is FieldKind.HEADERS:
                ids.append("header-key-0")
            else:
                ids.append(f"field-{spec.key}")
        return ids

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "add-name":
            self.query_one("#add-folder", Input).focus()
            return
        if event.input.id == "add-folder":
            self._focus_next("add-folder")
            return
        if event.input.id.startswith("field-"):
            self._focus_next(event.input.id)
            return
        index = self._row_index(event.input.id)
        if index is None:
            return
        if self._row_has_text(index):
            # Fila con contenido: avanzar (key -> value -> siguiente fila).
            if event.input.id.startswith("header-key"):
                self.query_one(f"#header-value-{index}", Input).focus()
                return
            next_index = index + 1
            if next_index >= self._row_count:
                await self._add_header_row()
            self.query_one(f"#header-key-{next_index}", Input).focus()
            return
        # Fila vacía: confirmar. Ojo: en tmux/la mayoría de terminales
        # Ctrl+Enter llega como Enter, así que Enter es la vía fiable.
        self._confirm()

    def _focus_next(self, current_id: str) -> None:
        """Avanza al siguiente input del modal; el último confirma.

        Enter en el último campo (o en la carpeta sin campos) confirma
        la descarga, como antes hacía la fila de headers vacía."""
        ids = self._field_ids()
        if not ids:
            self._confirm()
            return
        try:
            index = ids.index(current_id)
        except ValueError:
            # El input actual no es un campo (ej. la carpeta): primero.
            index = -1
        next_id = ids[index + 1] if index + 1 < len(ids) else None
        if next_id is None:
            self._confirm()
            return
        self.query_one(f"#{next_id}", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add-submit":
            self._confirm()
        elif event.button.id == "add-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_confirm(self) -> None:
        self._confirm()

    def _make_header_row(self, index: int) -> Container:
        return Container(
            Input(
                placeholder="Clave",
                id=f"header-key-{index}",
                classes="header-field header-key",
            ),
            Input(
                placeholder="Valor",
                id=f"header-value-{index}",
                classes="header-field header-value",
            ),
            classes="header-row",
        )

    async def _add_header_row(self) -> None:
        index = self._row_count
        self._row_count += 1
        fields = self.query_one("#headers-fields")
        await fields.mount(self._make_header_row(index))

    def _row_index(self, widget_id: str) -> int | None:
        for prefix in ("header-key-", "header-value-"):
            if widget_id.startswith(prefix):
                suffix = widget_id[len(prefix) :]
                return int(suffix) if suffix.isdigit() else None
        return None

    def _last_row_has_text(self) -> bool:
        return self._row_has_text(self._row_count - 1)

    def _row_has_text(self, index: int) -> bool:
        key = self.query_one(f"#header-key-{index}", Input).value.strip()
        value = self.query_one(f"#header-value-{index}", Input).value.strip()
        return bool(key or value)

    def _confirm(self) -> None:
        name = self.query_one("#add-name", Input).value.strip()
        folder = self.query_one("#add-folder", Input).value.strip()

        values: dict[str, str] = {}
        for spec in self._fields:
            if spec.kind is FieldKind.HEADERS:
                lines = []
                for index in range(self._row_count):
                    key = (
                        self.query_one(f"#header-key-{index}", Input)
                        .value.strip()
                    )
                    value = (
                        self.query_one(f"#header-value-{index}", Input)
                        .value.strip()
                    )
                    if key and value:
                        lines.append(f"{key}: {value}")
                values[spec.key] = "\n".join(lines)
            else:
                values[spec.key] = (
                    self.query_one(f"#field-{spec.key}", Input).value.strip()
                )

        self.dismiss(
            AddDownloadResult(
                output=DownloadOutput(
                    directory=Path(folder or self._default_directory).expanduser(),
                    filename=name or None,
                ),
                field_values=values,
            )
        )
