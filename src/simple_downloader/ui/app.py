from __future__ import annotations

import asyncio
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit
from uuid import UUID

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.widgets import Footer, Header, Input, ListView, Static

from simple_downloader.app.manager import DownloadManager
from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.domain.event import (
    DownloadProgressEvent,
    DownloadStateChangedEvent,
)
from simple_downloader.domain.models import (
    DownloadJob,
    DownloadOutput,
    DownloadRequest,
    DownloadState,
)
from simple_downloader.engines import EngineRegistry
from simple_downloader.engines.hls import HlsEngine
from simple_downloader.engines.http import HttpEngine
from simple_downloader.engines.ytdlp import YtDlpEngine
from simple_downloader.event import EventBus
from simple_downloader.executor import (
    ExecutableName,
    ExecutableSpec,
    ExecutorDetector,
    ExecutorRegistry,
)
from simple_downloader.infra.config import UserConfig, load_user_config
from simple_downloader.infra.http import AioHttpClient
from simple_downloader.process import AsyncProcessExecutor
from simple_downloader.sources import SourceProvider
from simple_downloader.ui.widgets import (
    AddDownloadModal,
    ConfirmModal,
    DetailsModal,
    Download,
    DownloadItem,
    DownloadStatus,
    StatsBar,
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
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        Binding("escape", "focus_list", "Lista"),
        Binding("q", "quit", "Salir"),
    ]

    def __init__(self) -> None:
        super().__init__()
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
        bus = EventBus()
        process_executor = AsyncProcessExecutor()
        detector = ExecutorDetector(executor=process_executor)

        executable = await detector.detect(
            executable_spec=ExecutableSpec(name=ExecutableName.YT_DLP.value)
        )
        executor_registry = ExecutorRegistry()
        executor_registry.register(executable=executable)

        source_provider = SourceProvider(
            executor_registry=executor_registry,
            process_executor=process_executor,
        )

        engine_registry = EngineRegistry()
        engine_registry.register(HlsEngine(http=AioHttpClient()))
        engine_registry.register(HttpEngine(http=AioHttpClient()))
        engine_registry.register(YtDlpEngine(source_provider=source_provider))

        scheduler = DownloadScheduler(event_bus=bus)
        scheduler.start()
        manager = DownloadManager(
            event_bus=bus,
            engine_registry=engine_registry,
            download_scheduler=scheduler,
        )

        bus.subscribe(DownloadStateChangedEvent, self._on_job_state)
        bus.subscribe(DownloadProgressEvent, self._on_progress)

        self._manager = manager
        self._scheduler = scheduler
        self._source_provider = source_provider
        self._config = load_user_config()

        self.query_one("#url-input", Input).focus()
        self._refresh_stats()
        self._toggle_empty()

    async def on_exit(self) -> None:
        if self._scheduler is not None:
            await self._scheduler.finish()

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
        if self._manager is not None:
            await self._manager.cancel(job_id=job_id)

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
        self._open_add_modal(url)

    # ── helpers ──────────────────────────────────────────────────────────

    def _open_add_modal(self, url: str) -> None:
        modal = AddDownloadModal(
            url,
            default_name=_base_name(url),
            directory=str(self._config.directory),
        )
        self._add_modal = modal
        self.push_screen(
            modal,
            callback=lambda output: (
                self._on_add_modal_closed(url, output) if output else None
            ),
        )
        if self._source_provider is not None:
            asyncio.create_task(self._fetch_metadata_for_modal(url, modal))

    async def _on_add_modal_closed(self, url: str, output: DownloadOutput) -> None:
        self._add_modal = None
        await self._add_job(url, output)

    async def _fetch_metadata_for_modal(
        self, url: str, modal: AddDownloadModal
    ) -> None:
        """Título real en segundo plano; solo actualiza si el usuario no editó."""
        try:
            source = self._source_provider.get_source(ExecutableName.YT_DLP)
            meta = await source.metadata(url)
        except Exception:
            return
        if meta.title and modal in self.screen_stack:
            modal.apply_external_title(_base_name(meta.title))

    async def _add_job(self, url: str, output: DownloadOutput | None = None) -> None:
        assert self._manager is not None
        try:
            title = (
                output.filename
                if output is not None and output.filename
                else _title_from_url(url)
            )
            job = await self._manager.enqueue(
                request=DownloadRequest(
                    url=url,
                    title=title,
                    output=output,
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
            downloaded_bytes=downloaded,
            speed_bps=speed or 0.0,
            status=_UI_TO_STATUS[job.state],
            eta_sec=eta,
            error_message=job.error,
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


def _elide(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return f"{text[: width - 1]}…"
