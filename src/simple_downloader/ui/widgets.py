from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from rich.text import Text
from textual.binding import Binding
from textual.containers import Container
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Input, Label, ListItem, Static

from simple_downloader.domain.models import DownloadOutput

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
    status: DownloadStatus = DownloadStatus.QUEUED
    eta_sec: float | None = None
    error_message: str | None = None
    url: str | None = None
    engine: str | None = None
    destination: str | None = None

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
        return Text.assemble(line1, "\n", line2)

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


class AddDownloadModal(ModalScreen[DownloadOutput | None]):
    """Añade una descarga: nombre y carpeta editables por descarga.

    Los valores iniciales vienen de la config de usuario; el resultado
    es un `DownloadOutput` (o `None` si se cancela).
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancelar"),
    ]

    def __init__(
        self,
        url: str,
        *,
        default_name: str = "",
        directory: str = "downloads",
    ) -> None:
        super().__init__()
        self._url = url
        self._default_name = default_name
        self._default_directory = directory

    def compose(self) -> ComposeResult:
        with Container(id="add-card"):
            yield Static(
                Text.assemble((" Añadir descarga", "bold white")),
                id="add-title",
            )
            yield Static(Text(self._url, style=DIM, no_wrap=False), id="add-url")
            yield Label("Nombre", classes="field-label")
            yield Input(
                value=self._default_name,
                placeholder="Nombre del archivo (sin extensión)",
                id="add-name",
            )
            yield Label("Carpeta", classes="field-label")
            yield Input(
                value=self._default_directory,
                placeholder="Carpeta de destino",
                id="add-folder",
            )
            yield Static(
                "[Enter] Añadir    [Tab] siguiente campo    [esc] Cancelar",
                id="modal-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#add-name", Input).focus()

    def apply_external_title(self, title: str) -> None:
        """Sobrescribe el nombre si el usuario aún no lo editó."""
        name = self.query_one("#add-name", Input)
        if name.value == self._default_name and title:
            name.value = title
            self._default_name = title

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if event.input.id == "add-name":
            self.query_one("#add-folder", Input).focus()
        else:
            self._confirm()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _confirm(self) -> None:
        name = self.query_one("#add-name", Input).value.strip()
        folder = self.query_one("#add-folder", Input).value.strip()
        self.dismiss(
            DownloadOutput(
                directory=Path(folder or self._default_directory).expanduser(),
                filename=name or None,
            )
        )
