from __future__ import annotations

from simple_downloader.domain.protocols import HttpClient


class AioHttpClient(HttpClient):
    """Cliente HTTP sobre aiohttp. Carga aiohttp de forma perezosa
    para que los tests de parser/crypt no necesiten la dependencia."""

    def __init__(
        self,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        chunk_size: int = 64 * 1024,
    ) -> None:
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError("aiohttp is required: uv add aiohttp") from exc

        self._aiohttp = aiohttp
        self._headers = headers
        self._timeout_sec = timeout
        # Sin total: una descarga lenta pero viva no se corta; el timeout
        # solo salta cuando no llegan datos (conexión o lectura estancada).
        self._timeout = aiohttp.ClientTimeout(
            total=None,
            connect=timeout,
            sock_connect=timeout,
            sock_read=timeout,
        )
        self._chunk_size = chunk_size

    def with_referer(self, referer: str) -> AioHttpClient:
        headers = {**(self._headers or {}), "referer": referer}
        return self.with_headers(headers)

    def with_headers(self, headers: dict[str, str]) -> AioHttpClient:
        return AioHttpClient(
            headers=headers, timeout=self._timeout_sec, chunk_size=self._chunk_size
        )

    async def get(self, url: str) -> bytes:
        async with self._aiohttp.ClientSession(
            headers=self._headers, timeout=self._timeout
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.read()

    async def check(self, url: str) -> int | None:
        """Verifica que el recurso exista y responda sin descargar el body.

        Pide bytes=0-0 y lee solo las cabeceras. Lanza (4xx/5xx, errores
        de red) si el recurso no es accesible; devuelve el tamaño total
        si el servidor lo informa. Si el servidor ignora el rango (200),
        Content-Length es igualmente el tamaño completo.
        """
        async with self._aiohttp.ClientSession(
            headers=self._headers, timeout=self._timeout
        ) as session:
            async with session.get(url, headers={"Range": "bytes=0-0"}) as response:
                response.raise_for_status()
                return _total_from_headers(response)

    async def size(self, url: str) -> int | None:
        """Tamaño total del recurso (best-effort, para la UI).

        Devuelve None si el servidor no da tamaño o falla la conexión;
        para saber si el recurso es accesible, usar `check`.
        """
        try:
            return await self.check(url)
        except Exception:
            return None

    async def get_range(self, url: str, start: int, end: int) -> bytes:
        headers = {**(self._headers or {}), "Range": f"bytes={start}-{end}"}
        async with self._aiohttp.ClientSession(
            headers=headers, timeout=self._timeout
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.read()

    async def stream(
        self,
        url: str,
        *,
        offset: int = 0,
        headers: dict[str, str] | None = None,
    ):
        """GET streaming: yield (status, total, chunk).

        - status: HTTP real de la respuesta. Con `offset > 0`: 206 = el
          servidor respetó el rango; 200 = lo ignoró (descarga desde 0,
          el parcial hay que descartarlo); 416 = ya está completo.
        - total: solo en el primer item (Content-Length / Content-Range).
        - El timeout total se desactiva para no cortar descargas largas;
          se aplica solo a la conexión y a cada chunk leído.
        """
        request_headers = {**(self._headers or {}), **(headers or {})}
        if offset > 0:
            request_headers["Range"] = f"bytes={offset}-"
        timeout = self._aiohttp.ClientTimeout(
            total=None, connect=self._timeout_sec, sock_read=self._timeout_sec
        )
        async with self._aiohttp.ClientSession(
            headers=request_headers, timeout=timeout
        ) as session:
            async with session.get(url) as response:
                if response.status == 416:
                    # El rango pedido no es satisfacible: archivo completo.
                    yield 416, _total_from_headers(response), b""
                    return
                response.raise_for_status()
                yield response.status, _total_from_headers(response), b""
                async for chunk in response.content.iter_chunked(self._chunk_size):
                    yield None, None, chunk


def _total_from_headers(response) -> int | None:
    """Tamaño total del recurso: Content-Range (206) o Content-Length."""
    content_range = response.headers.get("Content-Range")
    if content_range:
        total = content_range.rsplit("/", 1)[-1]
        if total.isdigit():
            return int(total)
    length = response.headers.get("Content-Length")
    if length is not None and length.isdigit():
        return int(length)
    return None


def describe_http_error(exc: BaseException) -> str | None:
    """Traduce errores de red/HTTP conocidos a mensajes legibles.

    Devuelve None si la excepción no es de aiohttp, para que el
    llamador use su propio formato de fallback."""
    # TimeoutError (asyncio/builtin en 3.11+): str() es vacío, hay que
    # traducirlo o el job queda con un error incomprensible.
    if isinstance(exc, TimeoutError):
        return "timeout: el servidor no respondió a tiempo"
    try:
        from aiohttp import (
            ClientConnectorError,
            ClientResponseError,
            ServerTimeoutError,
            TooManyRedirects,
        )
    except ImportError:
        return None

    if isinstance(exc, ClientResponseError):
        return f"HTTP {exc.status} {exc.message or ''} — acceso denegado o recurso no disponible".strip()
    if isinstance(exc, ClientConnectorError):
        return f"sin conexión con {exc.host}"
    if isinstance(exc, (ServerTimeoutError, TooManyRedirects)):
        return "timeout de red o demasiados redirects"
    return None
