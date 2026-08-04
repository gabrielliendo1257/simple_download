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
            raise ImportError(
                "aiohttp is required: uv add aiohttp"
            ) from exc

        self._aiohttp = aiohttp
        self._headers = headers
        self._timeout_sec = timeout
        self._timeout = aiohttp.ClientTimeout(total=timeout)
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

    async def stream(self, url: str):
        """GET streaming: yield (total_bytes, chunk). total_bytes solo
        en el primer item (de Content-Length)."""
        # El timeout total prohibiría descargas largas: se aplica solo
        # a la conexión y a cada chunk leído.
        timeout = self._aiohttp.ClientTimeout(
            total=None, connect=self._timeout_sec, sock_read=self._timeout_sec
        )
        async with self._aiohttp.ClientSession(
            headers=self._headers, timeout=timeout
        ) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                length = response.headers.get("Content-Length")
                total = int(length) if length is not None else None
                yield total, b""
                async for chunk in response.content.iter_chunked(
                    self._chunk_size
                ):
                    yield None, chunk