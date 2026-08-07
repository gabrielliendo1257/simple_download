from __future__ import annotations

import asyncio
from dataclasses import dataclass

from simple_downloader.app.manager import DownloadManager
from simple_downloader.app.scheduler import DownloadScheduler
from simple_downloader.db import SqliteRepository
from simple_downloader.engines import EngineRegistry
from simple_downloader.engines.hls import HlsEngine
from simple_downloader.engines.http import HttpEngine
from simple_downloader.engines.telegram import (
    TelegramClientProvider,
    TelegramEngine,
    TelegramNotAuthorizedError,
)
from simple_downloader.engines.ytdlp import YtDlpEngine
from simple_downloader.event import EventBus
from simple_downloader.executor import (
    ExecutableName,
    ExecutableSpec,
    ExecutorDetector,
    ExecutorRegistry,
)
from simple_downloader.infra.config import UserConfig, catalog_db_path, load_user_config
from simple_downloader.infra.http import AioHttpClient
from simple_downloader.process import AsyncProcessExecutor
from simple_downloader.sources import SourceProvider


@dataclass
class Backend:
    """Servicios compartidos por cualquier interfaz (TUI, Telegram...)."""

    event_bus: EventBus
    manager: DownloadManager
    scheduler: DownloadScheduler
    source_provider: SourceProvider
    engine_registry: EngineRegistry
    config: UserConfig
    telegram_provider: TelegramClientProvider | None = None


async def build_backend() -> Backend:
    """Construye y arranca el backend: config, yt-dlp, engines, scheduler.

    La UI solo recibe Backend y se suscribe al event_bus.
    """
    config = load_user_config()

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
    telegram_provider = TelegramClientProvider(config.telegram)
    if config.telegram.is_usable():
        # Conexión de Telegram en background: el cliente corre ya conectado
        # en su loop dedicado cuando arranque la primera descarga.
        asyncio.create_task(_start_telegram(telegram_provider))
    engine_registry.register(TelegramEngine(client_provider=telegram_provider))
    engine_registry.register(
        YtDlpEngine(
            source_provider=source_provider,
            cookies_from_browser=config.ytdlp.cookies_from_browser,
        )
    )

    # Catálogo persistente: los jobs sobreviven al cierre de la app.
    job_repository = SqliteRepository(catalog_db_path())

    scheduler = DownloadScheduler(event_bus=bus, job_repository=job_repository)
    scheduler.start()

    manager = DownloadManager(
        event_bus=bus,
        engine_registry=engine_registry,
        download_scheduler=scheduler,
        job_repository=job_repository,
    )
    await manager.load()

    return Backend(
        event_bus=bus,
        manager=manager,
        scheduler=scheduler,
        source_provider=source_provider,
        engine_registry=engine_registry,
        config=config,
        telegram_provider=telegram_provider,
    )


async def _start_telegram(telegram_provider) -> None:
    """Fire-and-forget con manejo de error: un fallo de arranque de
    Telegram no debe imprimir tracebacks ni matar la app."""
    try:
        await telegram_provider.start()
    except TelegramNotAuthorizedError:
        pass  # sin sesión es un estado normal: la TUI muestra el badge
    except Exception as exc:
        print(f"aviso: Telegram no arrancó: {exc}")
