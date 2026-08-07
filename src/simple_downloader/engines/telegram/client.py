from __future__ import annotations

import asyncio
import sqlite3
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


class TelegramNotAuthorizedError(RuntimeError):
    """No hay sesión autorizada: el cliente conectó pero nadie escaneó el QR.

    No es un error de config: la app debe ofrecer el login en runtime
    (QR en la TUI) en lugar de pedir teléfono/código por stdin."""

    def __init__(self) -> None:
        super().__init__(
            "Telegram no está autenticado: apretá Ctrl+T para escanear el QR"
        )


STATUS_IDLE = "idle"  # nunca se intentó conectar
STATUS_CONNECTING = "connecting"
STATUS_AUTHENTICATED = "authenticated"
STATUS_AUTH_REQUIRED = "auth_required"
STATUS_ERROR = "error"


class TelegramClientProvider:
    """Cliente Telethon aislado en su propio hilo y event loop.

    Telethon asume que posee el event loop: construye el cliente sobre
    `asyncio.get_event_loop()` y usa primitivas que dependen del loop
    donde se ejecutan. Si corre sobre el loop de la TUI (Textual) puede
    bloquearlo entero (la descarga se cuelga en QUEUED y la UI se congela).

    Por eso todas las operaciones se ejecutan en un hilo worker con un
    loop propio, y se exponen como corrutinas agnósticas al loop del
    llamante (bridge con `run_coroutine_threadsafe` + `wrap_future`).

    La sesión se persiste en `~/.config/simple-downloader/<session>.session`.
    Sin sesión no se pide teléfono/código por stdin (colgaría el hilo
    worker): el cliente conecta y queda en estado "auth_required"; la TUI
    ofrece el login por QR en runtime (`qr_begin`/`qr_wait`/`qr_refresh`).
    """

    def __init__(self, config: TelegramConfig | None = None) -> None:
        self._config = config
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any | None = None
        self._client_ready: asyncio.Future[Any] | None = None
        self._dl_slots: asyncio.Semaphore | None = None
        self._qr: Any | None = None
        self._status: str = STATUS_IDLE

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

        Sin sesión no falla ni pide nada: conecta y queda en
        `auth_required` (el login lo hace la TUI con el QR).
        """
        client = await self._ensure_connected()
        return client

    def status(self) -> str:
        """Estado de autenticación (leído desde cualquier hilo/loop).

        `idle`/`connecting` mientras conecta, `auth_required` sin sesión,
        `authenticated` con sesión válida, `error` si algo falló.
        """
        return self._status

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
        """Cliente conectado y autorizado (exige sesión válida)."""
        client = await self._ensure_connected()
        authorized = await self._submit(lambda: client.is_user_authorized())
        if not authorized:
            self._status = STATUS_AUTH_REQUIRED
            raise TelegramNotAuthorizedError()
        self._status = STATUS_AUTHENTICATED
        return client

    async def _ensure_connected(self) -> Any:
        """Cliente conectado (sin exigir autorización: sirve para el QR)."""
        if self._client is None:
            if self._client_ready is None:
                self._client_ready = asyncio.ensure_future(
                    self._submit(self._connect)
                )
            try:
                self._client = await self._client_ready
            except Exception:
                self._client_ready = None  # permite reintentar la conexión
                raise
        return self._client

    async def _connect(self) -> Any:
        if self._config is None or not self._config.is_usable():
            self._status = STATUS_ERROR
            raise ValueError(
                "telegram no está configurado: edita la sección telegram de "
                "~/.config/simple-downloader/config.json (enabled, api_id, api_hash)"
            )
        try:
            from telethon import TelegramClient
        except ImportError as exc:
            raise ImportError("telethon is required: uv add telethon") from exc

        try:
            session = _session_path(self._config.session_name)
            client = TelegramClient(
                str(session),
                api_id=self._config.api_id,
                api_hash=self._config.api_hash,
                # Sin sleeps silenciosos: los bloqueos los traducimos a
                # TelegramThrottledError y se avisan (flood_sleep_threshold=0).
                flood_sleep_threshold=0,
            )
        except sqlite3.OperationalError as exc:
            raise ValueError(
                f"no se pudo abrir la sesión de Telegram en {session}: "
                "el directorio no existe o no es escribible (revisá tu $HOME "
                "en Termux)"
            ) from exc
        self._dl_slots = asyncio.Semaphore(_MAX_CONCURRENT_DOWNLOADS)
        self._status = STATUS_CONNECTING
        try:
            await client.connect()
        except Exception as exc:
            self._status = STATUS_ERROR
            raise
        self._status = (
            STATUS_AUTHENTICATED
            if await client.is_user_authorized()
            else STATUS_AUTH_REQUIRED
        )
        return client

    # ── login QR (estado: una sesión dura ~30s; recrear al expirar) ─────

    async def qr_begin(self) -> str:
        """Crea un QR de login en el worker y devuelve su URL.

        Requiere que el cliente haya conectado (`start`); falla con el
        mensaje de config si Telegram no está habilitado.
        """
        await self._ensure_connected()
        return await self._submit(self._qr_create)

    async def qr_wait(self, timeout: float = 25.0) -> bool:
        """Espera (en el worker) a que escaneen el QR actual.

        `True` si quedó autenticado (la sesión queda guardada para
        siempre); `False` si expiró sin escanear (recrear con qr_refresh).
        """
        return await self._submit(lambda: self._qr_wait(timeout))

    async def qr_refresh(self) -> str:
        """Regenera el token del QR actual y devuelve su nueva URL."""
        return await self._submit(self._qr_refresh)

    async def _qr_create(self) -> str:
        client = self._client  # corre en el worker
        if client is None:
            return ""
        qr = await client.qr_login()
        self._qr = qr
        return qr.url

    async def _qr_wait(self, timeout: float) -> bool:
        qr = self._qr
        if qr is None:
            return False
        user = await qr.wait(timeout=timeout)
        if user is not None:
            self._qr = None
            self._status = STATUS_AUTHENTICATED
            return True
        return False

    async def _qr_refresh(self) -> str:
        qr = self._qr
        if qr is None:
            return ""
        await qr.recreate()
        return qr.url


def _session_path(session_name: str) -> Path:
    """Ruta del archivo de sesión de Telethon, creando el directorio.

    Telethon abre la sesión con SQLite y NO crea los directorios padre:
    si `~/.config/simple-downloader` no existe (o no es escribible, p.ej.
    en Termux con un HOME raro), sqlite falla con "unable to open
    database file" sin decir dónde está el archivo."""
    _SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return _SESSION_DIR / f"{session_name}.session"


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
