from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any, Callable, Coroutine

from simple_downloader.infra.config import TelegramConfig

_SESSION_DIR = Path.home() / ".config" / "simple-downloader"

# Telegram limita las conexiones simultáneas por cuenta: más de unas pocas
# descargas a la vez provoca bloqueos (flood wait ~32s). Este tope es
# independiente del scheduler y evita llegar a ese límite.
_MAX_CONCURRENT_DOWNLOADS = 2


class TelegramThrottledError(RuntimeError):
    """Telegram bloqueó temporalmente la cuenta (límite de conexiones o
    velocidad de descarga). No reintentar de inmediato: hay que esperar."""

    def __init__(self, wait_seconds: int, *, caused_by: str = "") -> None:
        self.wait_seconds = wait_seconds
        cause = f" ({caused_by})" if caused_by else ""
        super().__init__(
            f"Telegram bloqueó temporalmente la cuenta: esperá "
            f"{wait_seconds}s y reintentá{cause}"
        )


class TelegramClientProvider:
    """Cliente Telethon aislado en su propio hilo y event loop.

    Telethon asume que posee el event loop: construye el cliente sobre
    `asyncio.get_event_loop()` y usa primitivas que dependen del loop
    donde se ejecutan. Si corre sobre el loop de la TUI (Textual) puede
    bloquearlo entero (la descarga se cuelga en QUEUED y la UI se congela).

    Por eso todas las operaciones se ejecutan en un hilo worker con un
    loop propio, y se exponen como corrutinas agnósticas al loop del
    llamante (bridge con `run_coroutine_threadsafe` + `wrap_future`).

    La sesión se persiste en `~/.config/simple-downloader/<session>.session`:
    el primer arranque pide teléfono + código de forma interactiva
    (conviene hacerlo con `simple-downloader --telegram-login`); después
    la sesión se reutiliza sin intervención.
    """

    def __init__(self, config: TelegramConfig | None = None) -> None:
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any | None = None
        self._client_ready: asyncio.Future[Any] | None = None
        self._dl_slots: asyncio.Semaphore | None = None

    # ── hilo worker ─────────────────────────────────────────────────────

    def _ensure_worker(self) -> asyncio.AbstractEventLoop:
        """Arranca (una vez) el hilo worker con su loop propio."""
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_worker,
                name="telethon",
                daemon=True,
            )
            self._thread.start()
        return self._loop

    def _run_worker(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(
        self, factory: Callable[[], Coroutine[Any, Any, Any]]
    ) -> asyncio.Future:
        """Lanza `factory()` en el worker loop y devuelve un future agnóstico
        al loop del llamante. `factory` se ejecuta en el worker, así que
        Telethon nunca toca el loop de la TUI."""
        loop = self._ensure_worker()

        async def runner() -> Any:
            return await factory()

        fut = asyncio.run_coroutine_threadsafe(runner(), loop)
        return asyncio.wrap_future(fut)

    # ── API pública (segura desde cualquier loop) ───────────────────────

    async def start(self) -> Any:
        """Arranca el hilo worker y conecta el cliente (idempotente).

        Se invoca al arrancar la app (bootstrap) para que la conexión ya
        esté establecida cuando arranque una descarga; el hilo se queda
        corriendo en su loop dedicado entre descargas. Si se lanza en
        background, la primera descarga espera a que termine de conectar.
        """
        return await self._ensure_client()

    async def get_message(self, peer: str | int, message_id: int) -> Any:
        client = await self._ensure_client()
        return await self._submit(lambda: client.get_messages(peer, ids=message_id))

    async def download_to(
        self,
        message: Any,
        out_file: Path,
        offset: int = 0,
        progress_callback: Callable[[int, int], None] | None = None,
        waiting_callback: Callable[[bool], None] | None = None,
    ) -> None:
        """Descarga (o reanuda desde `offset`) el media escribiendo en
        `out_file`, abierto en modo append para no pisar el parcial.

        Todo corre en el hilo worker (Telethon); el progreso vuelve al
        loop del llamante por `call_soon_threadsafe`. El progreso reporta
        la posición absoluta en el archivo (`f.tell()`), así que al
        reanudar la barra continúa desde donde quedó.

        `waiting_callback(True/False)` avisa cuándo la descarga quedó
        esperando un turno del semáforo de conexiones (o lo consiguió)."""
        client = await self._ensure_client()
        main_loop = asyncio.get_running_loop()

        def on_progress(received: int, total: int) -> None:
            if progress_callback is not None:
                main_loop.call_soon_threadsafe(progress_callback, received, total)

        def on_waiting(waiting: bool) -> None:
            if waiting_callback is not None:
                main_loop.call_soon_threadsafe(waiting_callback, waiting)

        fut = self._submit(
            lambda: self._download_guarded(
                client, message, out_file, offset, on_progress, on_waiting
            )
        )
        try:
            await fut
        except asyncio.CancelledError:
            fut.cancel()  # cancela también la tarea en el worker loop
            raise

    async def _download_guarded(
        self,
        client: Any,
        message: Any,
        out_file: Path,
        offset: int,
        progress_callback: Callable[[int, int], None] | None,
        waiting_callback: Callable[[bool], None] | None = None,
    ) -> None:
        """Igual que `_download` pero con tope de descargas simultáneas:
        evita superar el límite de conexiones de Telegram y los flood waits.
        Si el semáforo está ocupado, avisa que quedó en espera."""
        slots = self._dl_slots
        if slots is None:
            slots = asyncio.Semaphore(_MAX_CONCURRENT_DOWNLOADS)
        if slots.locked() and waiting_callback is not None:
            waiting_callback(True)
        async with slots:
            if waiting_callback is not None:
                waiting_callback(False)
            await self._download(client, message, out_file, offset, progress_callback)

    async def _download(
        self,
        client: Any,
        message: Any,
        out_file: Path,
        offset: int,
        progress_callback: Callable[[int, int], None] | None,
    ) -> None:
        file = getattr(message, "file", None)
        total = getattr(file, "size", None) if file is not None else None
        # offset=0 trunca el parcial (fallback a descarga completa);
        # offset>0 continúa en modo append.
        with open(out_file, "wb" if offset == 0 else "ab") as handle:
            try:
                async for chunk in client.iter_download(message.media, offset=offset):
                    handle.write(chunk)
                    if progress_callback is not None:
                        progress_callback(handle.tell(), total)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise _translate_throttle(exc) from exc

    async def get_me(self) -> Any:
        client = await self._ensure_client()
        return await self._submit(lambda: client.get_me())

    async def disconnect(self) -> None:
        """Cierra el cliente y detiene el hilo worker (se usa al salir de
        la app o al terminar `--telegram-login`)."""
        client = self._client
        if client is not None:
            try:
                await self._submit(lambda: client.disconnect())
            except Exception:
                pass
            self._client = None
        self._stop_worker()

    def _stop_worker(self) -> None:
        loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)

    # ── conexión (corre en el worker loop) ──────────────────────────────

    async def _ensure_client(self) -> Any:
        if self._client is None:
            if self._client_ready is None:
                self._client_ready = asyncio.ensure_future(self._submit(self._connect))
            self._client = await self._client_ready
        return self._client

    async def _connect(self) -> Any:
        if self._config is None or not self._config.is_usable():
            raise ValueError(
                "telegram no está configurado: edita la sección telegram de "
                "~/.config/simple-downloader/config.json (enabled, api_id, api_hash)"
            )
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise ImportError("telethon is required: uv add telethon") from exc

        session = _SESSION_DIR / f"{self._config.session_name}.session"
        client = TelegramClient(
            str(session),
            api_id=self._config.api_id,
            api_hash=self._config.api_hash,
            # Sin sleeps silenciosos: los bloqueos los traducimos a
            # TelegramThrottledError y se avisan (flood_sleep_threshold=0).
            flood_sleep_threshold=0,
        )
        self._dl_slots = asyncio.Semaphore(_MAX_CONCURRENT_DOWNLOADS)
        await client.start()
        return client


def _translate_throttle(exc: Exception) -> Exception:
    """Convierte los bloqueos de Telegram en TelegramThrottledError.

    `FloodWaitError`: la cuenta pidió esperar N segundos (límite de
    peticiones/descargas). El error de "wait for a connection" (agotadas
    las conexiones del DC) llega como FloodWaitError con seconds=1.
    """
    try:
        from telethon.errors.rpcerrorlist import FloodWaitError
    except ImportError:
        return exc

    if isinstance(exc, FloodWaitError):
        return TelegramThrottledError(
            getattr(exc, "seconds", 30), caused_by="flood wait"
        )
    return exc
