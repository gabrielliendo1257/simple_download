from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, Input, ListView, Static

from simple_downloader.app.bootstrap import Backend, build_backend
from simple_downloader.app.manager import DownloadManager
from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.domain.event import (
    DownloadProgressEvent,
    DownloadStateChangedEvent,
)
from simple_downloader.domain.models import (
    DownloadContext,
    DownloadJob,
    DownloadOutput,
    DownloadRequest,
    DownloadState,
)
from simple_downloader.domain.options import FieldOption, FieldKind, ModalField
from simple_downloader.engines.telegram import (
    STATUS_AUTHENTICATED,
    STATUS_AUTH_REQUIRED,
    TelegramLink,
    TelegramNotAuthorizedError,
    parse_link,
)
from simple_downloader.event import EventBus
from simple_downloader.executor import ExecutableName
from simple_downloader.infra.config import UserConfig
from simple_downloader.sources import SourceProvider
from simple_downloader.ui.widgets import (
    AddDownloadModal,
    AddDownloadResult,
    ConfirmModal,
    DetailsModal,
    Download,
    DownloadItem,
    DownloadStatus,
    StatsBar,
    TelegramLoginModal,
    parse_headers,
)

_UI_TO_STATUS = {
    DownloadState.QUEUED: DownloadStatus.QUEUED,
    DownloadState.RUNNING: DownloadStatus.DOWNLOADING,
    DownloadState.PAUSED: DownloadStatus.PAUSED,
    DownloadState.COMPLETED: DownloadStatus.COMPLETED,
    DownloadState.FAILED: DownloadStatus.ERROR,
    DownloadState.CANCELLED: DownloadStatus.CANCELLED,
}

_STATE_LABELS = {
    DownloadState.QUEUED: "en cola",
    DownloadState.RUNNING: "en curso",
    DownloadState.PAUSED: "pausada",
    DownloadState.COMPLETED: "completada",
    DownloadState.FAILED: "con error",
    DownloadState.CANCELLED: "cancelada",
}

_PROGRESS_INTERVAL = 0.1  # máx. ~10 refrescos/s por fila
_STATS_INTERVAL = 0.25  # máx. 4 refrescos/s de la barra de resumen


class DownloadApp(App[None]):
    """TUI de descargas conectada al backend real."""

    TITLE = "Simple Downloader"
    SUB_TITLE = "gestor de descargas"
    CSS_PATH = "styles.tcss"

    BINDINGS = [
        Binding("a", "add_url", "Añadir URL"),
        Binding("p", "pause", "Pausar"),
        Binding("r", "resume", "Reanudar"),
        Binding("x", "cancel", "Cancelar"),
        Binding("d", "discard", "Descartar"),
        Binding("enter", "details", "Detalles"),
        Binding("ctrl+t", "telegram_login", "Login TG"),
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("escape", "focus_list", "Lista"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._backend: Backend | None = None
        self._manager: DownloadManager | None = None
        self._scheduler: DownloadScheduler | None = None
        self._source_provider: SourceProvider | None = None
        self._items: dict[str, DownloadItem] = {}
        self._jobs: dict[str, DownloadJob] = {}
        self._last_progress: dict[str, float] = {}
        self._last_stats = 0.0
        self._config = UserConfig.defaults()
        self._add_modal: AddDownloadModal | None = None

    # ── composición ───────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main"):
            yield Input(id="url-input", placeholder="Pega una URL y presiona Enter…")
            with Container(id="list-area"):
                yield ListView(id="download-list")
                yield Static(id="empty-state", markup=False)
            yield StatsBar(id="stats-bar")
        yield Footer()

    async def on_mount(self) -> None:
        backend = await build_backend()

        backend.event_bus.subscribe(DownloadStateChangedEvent, self._on_job_state)
        backend.event_bus.subscribe(DownloadProgressEvent, self._on_progress)

        self._backend = backend
        self._manager = backend.manager
        self._scheduler = backend.scheduler
        self._source_provider = backend.source_provider
        self._config = backend.config

        # Cursor fijo sin parpadeo (tmux/terminales no refrescan el blink)
        self.query_one("#url-input", Input).cursor_blink = False
        await self._seed_jobs()
        self.query_one("#url-input", Input).focus()
        self._refresh_stats()
        self._toggle_empty()
        self.set_interval(2.0, self._refresh_telegram_badge)

    async def _seed_jobs(self) -> None:
        """Jobs del catálogo persistido (SQLite) visibles desde el arranque."""
        if self._manager is None:
            return
        list_view = self.query_one("#download-list", ListView)
        for job in self._manager.list():
            key = str(job.id)
            self._jobs[key] = job
            self._items[key] = DownloadItem(self._to_ui(job), id=f"job-{job.id}")
        for key, item in self._items.items():
            await list_view.append(item)
        if self._items and list_view.index is None:
            list_view.index = 0

    async def on_exit(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.finish()
        telegram = (
            self._backend.telegram_provider if self._backend is not None else None
        )
        if telegram is not None:
            await telegram.disconnect()

    # ── observadores de eventos del backend ──────────────────────────────
    async def _on_job_state(self, event: DownloadStateChangedEvent) -> None:
        job = self._find_job(event.job_id)
        if job is None:
            return
        item = self._items.get(str(event.job_id))
        if item is not None:
            item.update_download(self._to_ui(job))
        self._refresh_stats()

    async def _on_progress(self, event: DownloadProgressEvent) -> None:
        job = self._find_job(event.job_id)
        if job is None:
            return
        now = time.monotonic()
        key = str(event.job_id)
        if now - self._last_progress.get(key, 0.0) < _PROGRESS_INTERVAL:
            return
        self._last_progress[key] = now

        item = self._items.get(key)
        if item is not None:
            item.update_download(self._to_ui(job))
        if now - self._last_stats >= _STATS_INTERVAL:
            self._last_stats = now
            self._refresh_stats()

    # ── acciones de la TUI ───────────────────────────────────────────────

    def action_add_url(self) -> None:
        self.query_one("#url-input", Input).focus()

    def action_focus_list(self) -> None:
        self.query_one("#download-list", ListView).focus()

    def action_telegram_login(self) -> None:
        backend = self._backend
        if backend is None or backend.telegram_provider is None:
            return
        if not backend.config.telegram.is_usable():
            self.notify(
                "Telegram no está configurado: edita la sección telegram de "
                "~/.config/simple-downloader/config.json",
                severity="warning",
            )
            return
        self.push_screen(
            TelegramLoginModal(backend.telegram_provider),
            callback=lambda ok: (
                self.notify("Sesión de Telegram iniciada.") if ok else None
            ),
        )

    def _refresh_telegram_badge(self) -> None:
        backend = self._backend
        provider = backend.telegram_provider if backend is not None else None
        if provider is None:
            return
        if provider.status() == STATUS_AUTHENTICATED:
            badge = "TG: ✓"
        elif provider.status() == STATUS_AUTH_REQUIRED:
            badge = "TG: ⚠ Ctrl+T"
        else:
            badge = None
        self.sub_title = (
            f"gestor de descargas  ·  {badge}" if badge else "gestor de descargas"
        )

    async def action_pause(self) -> None:
        job = self._selected_job()
        if not self._require_selection(job):
            return
        if job.state is not DownloadState.RUNNING:
            self._notify_invalid("Pausar", job)
            return
        await self._manager.pause(job_id=job.id)

    async def action_resume(self) -> None:
        job = self._selected_job()
        if not self._require_selection(job):
            return
        if job.state is not DownloadState.PAUSED:
            self._notify_invalid("Reanudar", job)
            return
        await self._manager.resume(job_id=job.id)

    async def action_cancel(self) -> None:
        job = self._selected_job()
        if not self._require_selection(job):
            return
        if job.state not in (
            DownloadState.RUNNING,
            DownloadState.PAUSED,
            DownloadState.QUEUED,
        ):
            self._notify_invalid("Cancelar", job)
            return

        title = job.request.title or _title_from_url(job.request.url)
        job_id = job.id
        self.push_screen(
            ConfirmModal(
                "Cancelar descarga",
                f'¿Cancelar "{_elide(title, 40)}"?\nEl archivo parcial se descarta.',
            ),
            callback=lambda confirmed: (
                asyncio.create_task(self._cancel_confirmed(job_id))
                if confirmed
                else None
            ),
        )

    async def _cancel_confirmed(self, job_id: UUID) -> None:
        try:
            if self._manager is not None:
                await self._manager.cancel(job_id=job_id)
        except Exception as exc:
            # Tarea fire-and-forget: nunca debe soltar un traceback.
            self.notify(f"No se pudo cancelar: {exc}", severity="error")

    async def action_discard(self) -> None:
        job = self._selected_job()
        if not self._require_selection(job):
            return
        if job.state not in (
            DownloadState.COMPLETED,
            DownloadState.FAILED,
            DownloadState.CANCELLED,
        ):
            self._notify_invalid("Descartar", job)
            return

        key = str(job.id)
        item = self._items.pop(key, None)
        if item is not None:
            await self.query_one("#download-list", ListView).remove_children([item])
        self._jobs.pop(key, None)
        if self._manager is not None:
            await self._manager.remove(job.id)
        self._toggle_empty()
        self._refresh_stats()

    async def action_details(self) -> None:
        job = self._selected_job()
        if not self._require_selection(job):
            return
        self.push_screen(DetailsModal(self._to_ui(job)))

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter en la lista: abre los detalles del job."""
        event.stop()
        await self.run_action("details")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url or self._manager is None:
            return
        event.input.value = ""
        self.notify("Resolviendo metadatos de la URL…")
        asyncio.create_task(self._resolve_and_open(url))

    # ── helpers ──────────────────────────────────────────────────────────

    async def _resolve_and_open(self, url: str) -> None:
        """Dos estrategias antes de abrir el modal de añadir:

        1. Metadata: título real cuando la app puede resolverla.
        2. Validación ligera por engine: la URL es genuina y descargable
           (playlist m3u8 real, servidor responde, mensaje TG existe).

        Si la metadata no está disponible (muy común en HLS/descarga
        directa) NO bloquea: se valida y se abre con nombre por defecto.
        Solo se notifica error si la validación también falla.
        """
        title = None
        try:
            title = await self._resolve_title(url)
        except Exception:
            title = None

        if title is None:
            try:
                await self._validate_url(url)
            except Exception as exc:
                self.notify(f"No se pudo resolver la URL: {exc}", severity="error")
                return

        options: dict[str, list[FieldOption]] = {}
        engine = self._engine_for(url)
        if engine is not None:
            modal_options = getattr(engine, "modal_options", None)
            if modal_options is not None:
                try:
                    options = await modal_options(url)
                except Exception:
                    options = {}  # sin opciones: el campo CHOICE no se muestra

        self._open_add_modal(url, title, options)

    async def _resolve_title(self, url: str) -> str | None:
        """Estrategia 1: metadata. None = sin título conocido."""
        link = parse_link(url)
        if link is not None:
            return await self._resolve_telegram_title(link)
        engine = self._engine_for(url)
        if engine is None or engine.name != "yt-dlp":
            # HLS y descarga directa no tienen metadata previa: solo
            # validación (strategy 2), sin esperar a yt-dlp.
            return None
        return await self._resolve_web_title(url)

    def _engine_for(self, url: str):
        backend = self._backend
        if backend is None:
            return None
        try:
            return backend.engine_registry.engine_for(url)
        except Exception:
            return None

    async def _validate_url(self, url: str) -> None:
        """Estrategia 2: verificación ligera de que es descargable."""
        engine = self._engine_for(url)
        if engine is None:
            raise RuntimeError("ningún motor soporta esta URL")
        validate = getattr(engine, "validate", None)
        if validate is None:
            return
        await validate(url)

    async def _resolve_telegram_title(self, link: TelegramLink) -> str | None:
        """Nombre real que reporta Telegram (si lo da); None = aleatorio.

        Los mensajes de texto (o con preview de página) no tienen `file`
        y no son descargables: se rechazan aquí para que la validación
        del engine dispare la notificación de error.
        """
        provider = (
            self._backend.telegram_provider if self._backend is not None else None
        )
        if provider is None:
            raise RuntimeError("Telegram no está configurado")
        try:
            message = await provider.get_message(link.peer, link.message_id)
        except TelegramNotAuthorizedError:
            raise ValueError(
                "Telegram no está autenticado: Ctrl+T para escanear el QR"
            ) from None
        if message is None or getattr(message, "file", None) is None:
            raise ValueError(f"el mensaje {link.message_id} no tiene media")
        return _telegram_media_name(message) or None

    async def _resolve_web_title(self, url: str) -> str | None:
        engine = self._engine_for(url)
        metadata = getattr(engine, "metadata", None)
        if metadata is not None:
            meta = await metadata(url)
        else:
            source = self._source_provider.get_source(ExecutableName.YT_DLP)
            meta = await source.metadata(url)
        return _base_name(meta.title) if meta.title else None

    def _open_add_modal(
        self, url: str, title: str | None, options: dict[str, list[FieldOption]] | None = None
    ) -> None:
        if title:
            default_name = title
        elif parse_link(url) is not None:
            # Telegram: nombre aleatorio de entrada; el real llega después
            # por metadata (o se queda el aleatorio si no hay nombre).
            default_name = secrets.token_hex(4)
        else:
            default_name = _base_name(url)

        # El modal se construye según el engine que resolvió la URL:
        # solo muestra los campos que le sirven (cookies en yt-dlp,
        # segmentos en HLS, nada en Telegram...). Los campos CHOICE se
        # completan en runtime con las opciones de la URL resuelta.
        fields: list[ModalField] = []
        engine = self._engine_for(url)
        if engine is not None:
            fields = [
                spec
                for spec in engine.modal_fields()
                if spec.kind is not FieldKind.CHOICE
                or (options or {}).get(spec.key)
            ]
            fields = [
                replace(spec, options=tuple((options or {}).get(spec.key, [])))
                if spec.kind is FieldKind.CHOICE
                else spec
                for spec in fields
            ]

        modal = AddDownloadModal(
            url,
            default_name=default_name,
            directory=str(self._config.directory),
            fields=fields,
        )
        self._add_modal = modal
        self.push_screen(
            modal,
            callback=lambda result: (
                self._on_add_modal_closed(url, result) if result else None
            ),
        )

    async def _on_add_modal_closed(self, url: str, result: AddDownloadResult) -> None:
        self._add_modal = None
        await self._add_job(url, result.output, result.field_values)

    async def _add_job(
        self,
        url: str,
        output: DownloadOutput | None = None,
        field_values: dict[str, str] | None = None,
    ) -> None:
        assert self._manager is not None
        try:
            title = (
                output.filename
                if output is not None and output.filename
                else _title_from_url(url)
            )
            context = context_from_fields(field_values)
            format_id = format_id_from_fields(field_values)
            job = await self._manager.enqueue(
                request=DownloadRequest(
                    url=url,
                    title=title,
                    output=output,
                    context=context,
                    format_id=format_id,
                )
            )
            self._jobs[str(job.id)] = job

            item = DownloadItem(self._to_ui(job), id=f"job-{job.id}")
            self._items[str(job.id)] = item
            list_view = self.query_one("#download-list", ListView)
            await list_view.append(item)
            if list_view.index is None:
                list_view.index = 0

            await self._manager.start(job_id=job.id)
        except Exception as exc:
            # Defensa en profundidad: ningún fallo del backend puede
            # romper la TUI; el job queda señalado en la lista.
            self.notify(f"No se pudo añadir la descarga: {exc}", severity="error")
            return

        self._toggle_empty()
        self._refresh_stats()

    def _selected_job(self) -> DownloadJob | None:
        list_view = self.query_one("#download-list", ListView)
        item = list_view.highlighted_child
        if item is None:
            return None
        job_id = str(item.id).removeprefix("job-")
        return self._jobs.get(job_id)

    def _require_selection(self, job: DownloadJob | None) -> bool:
        if job is not None:
            return True
        self.notify("Selecciona una descarga primero.", severity="information")
        return False

    def _notify_invalid(self, action: str, job: DownloadJob) -> None:
        label = _STATE_LABELS[job.state]
        self.notify(
            f"No se puede {action.lower()} una descarga {label}.",
            severity="warning",
        )

    def _find_job(self, job_id: UUID) -> DownloadJob | None:
        if self._manager is None:
            return None
        return self._manager.find(job_id)

    def _to_ui(self, job: DownloadJob) -> Download:
        progress = job.progress
        total = progress.total_bytes if progress else None
        downloaded = progress.downloaded_bytes if progress else 0
        speed = progress.speed_bps if progress else 0.0

        eta = None
        if speed and total and total > downloaded:
            try:
                total = int(total)
                downloaded = int(downloaded)
                eta = (total - downloaded) / speed
            except (TypeError, ValueError):
                total = None
                downloaded = None
                eta = None

        output = job.request.output
        if output is None:
            destination = None
        elif output.filename:
            destination = str(output.directory / output.filename)
        else:
            destination = str(output.directory)

        return Download(
            filename=job.request.title or _title_from_url(job.request.url),
            url=job.request.url,
            engine=job.engine,
            destination=destination,
            download_id=str(job.id),
            total_bytes=total or 0,
            downloaded_bytes=int(downloaded) or 0,
            speed_bps=float(speed) or 0.0,
            segments_done=progress.segments_done if progress else None,
            segments_total=progress.segments_total if progress else None,
            status=_UI_TO_STATUS[job.state],
            eta_sec=eta,
            error_message=job.error,
            notice=job.notice,
        )

    def _refresh_stats(self) -> None:
        jobs = list(self._jobs.values())
        stats = self.query_one("#stats-bar", StatsBar)
        stats.update_stats(
            total=len(jobs),
            active=sum(1 for j in jobs if j.state is DownloadState.RUNNING),
            queued=sum(1 for j in jobs if j.state is DownloadState.QUEUED),
            completed=sum(1 for j in jobs if j.state is DownloadState.COMPLETED),
            failed=sum(1 for j in jobs if j.state is DownloadState.FAILED),
            speed=sum(
                (j.progress.speed_bps or 0)
                for j in jobs
                if j.progress is not None and not isinstance(j.progress.speed_bps, str)
            ),
        )

    def _toggle_empty(self) -> None:
        has_jobs = bool(self._items)
        self.query_one("#empty-state").display = not has_jobs


def context_from_fields(values: dict[str, str] | None) -> DownloadContext | None:
    """Traduce el vocabulario compartido del modal a DownloadContext.

    Los engines declaran los campos que les sirven (`modal_fields`)
    con keys de `domain/options`; este es el único lugar que traduce
    esos valores a contexto de descarga. Lanza ValueError con mensaje
    legible si un valor es inválido (ej. paralelismo no numérico).
    """
    if not values:
        return None

    parallel_raw = (values.get("max_parallel_segments") or "").strip()
    parallel = 6
    if parallel_raw:
        if not parallel_raw.isdigit() or int(parallel_raw) < 1:
            raise ValueError(
                f"'segmentos en paralelo' debe ser un número ≥ 1 "
                f"(recibí '{parallel_raw}')"
            )
        parallel = int(parallel_raw)

    return DownloadContext(
        headers=parse_headers(values.get("headers", "")),
        cookies_path=values.get("cookies_path") or None,
        user_agent=values.get("user_agent") or None,
        max_parallel_segments=parallel,
    )


def format_id_from_fields(values: dict[str, str] | None) -> str | None:
    """Extrae el formato elegido del modal (campo CHOICE de yt-dlp).

    Va directo a `DownloadRequest.format_id` (no al contexto HTTP) y de
    ahí a `-f <format_id>` en el subprocess de yt-dlp."""
    if not values:
        return None
    return values.get("format_id") or None


def _title_from_url(url: str) -> str:
    """Nombre de archivo derivado de la URL, con fallback a la URL completa."""
    name = unquote(Path(urlsplit(url).path).name)
    return name or url


def _base_name(url: str) -> str:
    """Nombre sin extensión: prellenado del campo 'Nombre' en el modal."""
    name = _title_from_url(url)
    if Path(name).suffix:
        return Path(name).stem
    return name


def _telegram_media_name(message) -> str:
    """Nombre del documento según Telegram, o un nombre aleatorio si no da
    nombre (mantiene la extensión del media cuando Telethon la conoce)."""
    if message is not None:
        file = getattr(message, "file", None)
        file_name = getattr(file, "name", None) if file is not None else None
        if isinstance(file_name, str) and file_name:
            return file_name
    media_ext = getattr(file, "ext", "") if message is not None else ""
    suffix = media_ext.lstrip(".") if isinstance(media_ext, str) and media_ext else ""
    return secrets.token_hex(4) + (f".{suffix}" if suffix else "")


def _elide(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return f"{text[: width - 1]}…"
