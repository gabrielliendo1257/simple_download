from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from simple_downloader.domain.models import DownloadProgress, DownloadResult
from simple_downloader.engines.common import discard_partial
from simple_downloader.engines.telegram.client import TelegramThrottledError


@dataclass
class TelegramDownloadTask:
    """Descarga del media de un mensaje de Telegram.

    A diferencia de HLS (muchos segmentos en paralelo), aquí el archivo
    es **un solo segmento**: Telethon ya optimiza el download con su
    propio pipe, así que el paralelismo solo añadiría latencia.

    Escribe directamente en `out_file` (igual que `HttpTask`/`HlsTask`):
    el parcial queda en disco, así que pausar mantiene el progreso y
    reanudar continúa desde el tamaño actual del archivo (nunca desde
    cero). El offset es el único "estado" necesario y vive en el propio
    archivo parcial.

    `title` es el nombre real del archivo que reportó Telegram (si lo
    reportó): el manager lo usa para renombrar el job en la UI.
    """

    message: Any  # telethon.Message
    out_file: Path
    provider: Any  # TelegramClientProvider (Telethon en su propio hilo)
    title: str = ""

    def __post_init__(self) -> None:
        self._started = False
        self._change: asyncio.Event | None = None
        self._done: asyncio.Event | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._written: int = 0
        self._total: int | None = None
        self.resume_fallback: bool = False
        self.resume_fallback_reason: str | None = None

    def _start(self) -> asyncio.Task[None]:
        if not self._started:
            self._started = True
            self._change = asyncio.Event()
            self._done = asyncio.Event()

        if self._run_task is None:
            self._run_task = asyncio.create_task(self._run())

        return self._run_task

    async def _run(self) -> None:
        try:
            offset = self._resume_offset()
            try:
                await self.provider.download_to(
                    self.message,
                    out_file=self.out_file,
                    offset=offset,
                    progress_callback=self._on_progress,
                )
            except asyncio.CancelledError:
                raise
            except TelegramThrottledError:
                # Bloqueo de Telegram: no reiniciar desde cero (haría más
                # peticiones); falla con el mensaje de espera.
                raise
            except Exception:
                if offset > 0:
                    # No se pudo continuar desde el offset (por ejemplo, el
                    # archivo cambió en el servidor): fallback a descarga
                    # completa, avisando al usuario.
                    self.resume_fallback = True
                    self.resume_fallback_reason = (
                        f"no se pudo reanudar desde el byte {offset}; "
                        "se reinició la descarga"
                    )
                    await self.provider.download_to(
                        self.message,
                        out_file=self.out_file,
                        offset=0,
                        progress_callback=self._on_progress,
                    )
                else:
                    raise
        except asyncio.CancelledError:
            raise
        except Exception:
            # El parcial queda en disco: reanudar continuará desde ahí.
            raise
        finally:
            self._done.set()

    def _resume_offset(self) -> int:
        """Bytes ya escritos en disco; si el archivo está completo, no
        re-descarga nada (offset == total)."""
        file = getattr(self.message, "file", None)
        size = getattr(file, "size", None) if file is not None else None
        self._total = size

        if not self.out_file.exists():
            return 0
        written = self.out_file.stat().st_size
        if self._total is not None:
            return min(written, self._total)
        return written

    def _on_progress(self, received: int, total: int) -> None:
        # `received` es la posición absoluta en el archivo (f.tell()).
        self._written = received
        self._total = total if total is not None else self._total
        self._change.set()

    async def progress(self) -> AsyncIterator[DownloadProgress]:
        run_task = self._start()
        written = 0

        while not self._done.is_set():
            done_waiter = asyncio.create_task(self._done.wait())
            change_waiter = asyncio.create_task(self._change.wait())
            await asyncio.wait(
                {done_waiter, change_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
            change_waiter.cancel()
            done_waiter.cancel()

            self._change.clear()
            if self._written != written:
                written = self._written
                yield DownloadProgress(
                    downloaded_bytes=written, total_bytes=self._total
                )

        await run_task

    async def finalize(self) -> DownloadResult:
        await self._start()
        return DownloadResult(exit_code=0, output=self.out_file)

    async def pause(self) -> None:
        await self._stop()

    async def cancel(self) -> None:
        # Cancelar abandona la descarga: el parcial no se conserva.
        await self._stop()
        discard_partial(self.out_file)

    async def _stop(self) -> None:
        """Detiene la transferencia; el parcial queda en disco para reanudar."""
        if self._run_task is not None and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
