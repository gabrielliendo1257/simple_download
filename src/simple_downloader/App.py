from __future__ import annotations

import asyncio
import random
from datetime import datetime
from typing import Iterable

from rich.console import Group
from rich.panel import Panel
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Digits, Footer, Header, ListItem, ListView, Static

from simple_downloader.app.manager import DownloadManager
from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.engines import EngineRegistry
from simple_downloader.engines.hls import HlsEngine
from simple_downloader.engines.ytdlp import YtDlpEngine
from simple_downloader.event import EventBus
from simple_downloader.executor import (
    ExecutableName,
    ExecutableSpec,
    ExecutorDetector,
    ExecutorRegistry,
)
from simple_downloader.infra.http import AioHttpClient
from simple_downloader.process import AsyncProcessExecutor
from simple_downloader.sources import SourceProvider
from simple_downloader.ui.widgets import Download, DownloadItem, DownloadStatus


# ──────────────────────────────────────────────────────────────────────
# APP (mock, solo para desarrollo de widgets sin backend)
# ──────────────────────────────────────────────────────────────────────
class DownloadApp(App):
    """UI de descargas — dark, minimalista, funcional."""

    CSS = """
    Screen {
        background: #0d1117;
    }

    DownloadApp {
        background: #0d1117;
        color: #c9d1d9;
    }

    Header {
        background: #161b22;
        color: #58a6ff;
        text-style: bold;
        padding: 0 1;
    }

    Footer {
        background: #161b22;
        color: #8b949e;
    }

    #main-container {
        padding: 1 2;
        height: 100%;
    }

    #stats-bar {
        height: 3;
        background: #161b22;
        padding: 0 2;
        margin-bottom: 1;
    }

    .stat-label {
        color: #8b949e;
    }
    .stat-value {
        color: #58a6ff;
        text-style: bold;
    }

    #download-list {
        height: 1fr;
        border: none;
        background: transparent;
    }

    ListView {
        background: transparent;
    }

    ListItem {
        background: #0d1117;
        padding: 0 0;
        margin: 0 0;
    }

    ListItem:focus {
        background: #161b22;
    }

    ListItem:hover {
        background: #1c2128;
    }

    #empty-state {
        color: #484f58;
        text-style: italic;
        padding: 2 2;
        height: 100%;
        content-align: center middle;
    }

    .separator {
        color: #21262d;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Salir"),
        Binding("r", "refresh", "Refrescar"),
    ]

    def __init__(self, downloads: Iterable[Download] | None = None) -> None:
        super().__init__()
        self._downloads: list[Download] = list(downloads) if downloads else []
        self._downloads_items: dict[str, DownloadItem] = {}
        self._simulating = False

        self.download_manager: DownloadManager | None = None
        self.download_scheduler: DownloadScheduler | None = None
        self.source: SourceProvider | None = None
        self.event_bus: EventBus | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-container"):
            yield Horizontal(id="stats-bar")
            yield ListView(id="download-list")
            yield Horizontal(id="input-container")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "Descargas"
        self.build_list()
        self.build_stats()

        self.event_bus = EventBus()
        executor_registry = ExecutorRegistry()
        async_process_executor = AsyncProcessExecutor()
        detector = ExecutorDetector(executor=async_process_executor)

        executable = await detector.detect(
            executable_spec=ExecutableSpec(name=ExecutableName.YT_DLP.value)
        )
        executor_registry.register(executable=executable)

        source_provider = SourceProvider(
            executor_registry=executor_registry,
            process_executor=async_process_executor,
        )

        engine_registry = EngineRegistry()
        engine_registry.register(HlsEngine(http=AioHttpClient()))
        engine_registry.register(YtDlpEngine(source_provider=source_provider))

        self.source = source_provider
        self.download_scheduler = DownloadScheduler(
            event_bus=self.event_bus,
        )
        self.download_scheduler.start()
        self.download_manager = DownloadManager(
            event_bus=self.event_bus,
            engine_registry=engine_registry,
            download_scheduler=self.download_scheduler,
        )

        if self._downloads:
            self.call_later(self._simulate_downloads)

    async def on_exit(self):
        print("Cerrando la App")
        if self.download_scheduler:
            await self.download_scheduler.finish()

    def build_list(self) -> None:
        lv = self.query_one("#download-list", ListView)
        lv.clear()
        if not self._downloads:
            lv.mount(Static("  No hay descargas activas", id="empty-state"))
            return
        for dl in self._downloads:
            item = DownloadItem(dl, id=dl.download_id)
            self._downloads_items[dl.download_id] = item
            lv.mount(item)

    def build_stats(self) -> None:
        total = len(self._downloads)
        active = sum(
            1 for d in self._downloads if d.status == DownloadStatus.DOWNLOADING
        )
        completed = sum(
            1 for d in self._downloads if d.status == DownloadStatus.COMPLETED
        )
        errors = sum(1 for d in self._downloads if d.status == DownloadStatus.ERROR)
        speed = sum(d.speed_bps for d in self._downloads)

        speed_str = Download.size_str(int(speed)) + "/s" if speed > 0 else "0 B/s"

        stats = [
            ("Total", str(total)),
            ("Activas", str(active)),
            ("Completadas", str(completed)),
            ("Errores", str(errors)),
            ("Velocidad", speed_str),
        ]
        pieces: list[Text | str] = []
        for i, (label, value) in enumerate(stats):
            if i:
                pieces.append(Text("  │  ", "dim white"))
            pieces.append(Text(f"{label}: ", "dim white"))
            pieces.append(Text(value, "bold cyan"))
        bar = self.query_one("#stats-bar", Horizontal)
        try:
            widget = bar.query_one("#stats-text", Static)
            widget.update(Text.assemble(*pieces))
        except Exception:
            bar.mount(Static(Text.assemble(*pieces), id="stats-text"))

    @work(thread=False)
    async def _simulate_downloads(self) -> None:
        if self._simulating:
            return
        self._simulating = True
        print("downloads: ", self._downloads)
        try:
            while self._simulating and any(
                d.status == DownloadStatus.DOWNLOADING for d in self._downloads
            ):
                await asyncio.sleep(0.3)
                any_active = False
                for d in self._downloads:
                    if d.status != DownloadStatus.DOWNLOADING:
                        continue
                    chunk = random.randint(500_000, 5_000_000)
                    d.downloaded_bytes = min(d.downloaded_bytes + chunk, d.total_bytes)
                    remaining = d.total_bytes - d.downloaded_bytes
                    d.speed_bps = chunk / 0.3
                    d.eta_sec = remaining / d.speed_bps if d.speed_bps > 0 else None
                    if d.downloaded_bytes >= d.total_bytes:
                        d.status = DownloadStatus.COMPLETED
                    any_active = True
                    self._downloads_items[d.download_id].refresh()

                if not any_active:
                    break

                self.build_stats()
        finally:
            self._simulating = False

    def mock_data(self) -> None:
        """Carga datos ficticios (llamar antes de app.run())."""
        self._downloads = [
            Download(
                download_id="one",
                filename="ubuntu-24.04-desktop-amd64.iso",
                total_bytes=5_734_283_264,
                downloaded_bytes=1_234_567_890,
                speed_bps=12_500_000,
                status=DownloadStatus.DOWNLOADING,
            ),
            Download(
                download_id="dos",
                filename="archlinux-2025.06.01-x86_64.iso",
                total_bytes=892_338_176,
                downloaded_bytes=892_338_176,
                status=DownloadStatus.COMPLETED,
            ),
            Download(
                download_id="tres",
                filename="debian-12-bookworm-amd64-DVD-1.iso",
                total_bytes=3_900_000_000,
                downloaded_bytes=500_000_000,
                speed_bps=8_200_000,
                status=DownloadStatus.DOWNLOADING,
            ),
            Download(
                download_id="cuatro",
                filename="fedora-workstation-40-1.14-x86_64.iso",
                total_bytes=2_500_000_000,
                downloaded_bytes=0,
                status=DownloadStatus.QUEUED,
            ),
            Download(
                download_id="cinco",
                filename="nixos-gnome-24.11-x86_64-linux.iso",
                total_bytes=2_100_000_000,
                downloaded_bytes=1_900_000_000,
                speed_bps=3_100_000,
                status=DownloadStatus.PAUSED,
            ),
            Download(
                download_id="seis",
                filename="manjaro-xfce-24.2-x86_64.iso",
                total_bytes=3_200_000_000,
                downloaded_bytes=1_100_000_000,
                speed_bps=0,
                status=DownloadStatus.ERROR,
                error_message="Conexión perdida — reintentando…",
            ),
        ]


class ClockApp(App):
    CSS = """
    Screen { align: center middle; }
    Digits { width: auto; }
    """

    def __init__(self):
        super().__init__()
        self.clock_id = "clock"

    def compose(self) -> ComposeResult:
        yield Digits(id=self.clock_id, value="")

    def on_ready(self) -> None:
        self.update_clock()
        self.set_interval(1, self.update_clock)

    def update_clock(self) -> None:
        clock = datetime.now().time()
        self.query_one(f"#{self.clock_id}", Digits).update(f"{clock:%T}")


if __name__ == "__main__":
    app = DownloadApp()
    app.mock_data()
    app.run()
    # app = ClockApp()
    # app.run()
