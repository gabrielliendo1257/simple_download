from __future__ import annotations

from urllib.parse import urljoin

from simple_downloader.domain.protocols import HttpClient
from simple_downloader.engines.hls.crypt import aes_128_cbc_decrypt
from simple_downloader.engines.hls.models import Segment

_PNG_END = b"IEND"
_PNG_END_TRAILER = 8
_TS_SYNC = 0x47
_TS_PACKET = 188


def unwrap_ts(payload: bytes, sync_samples: int = 6) -> bytes:
    """Extrae el stream TS escondido tras el chunk IEND de un PNG.

    El sitio envuelve cada segmento en un PNG y antepone basura
    aleatoria: hay que localizar el primer paquete TS (sync 0x47)
    validando un estride de 188 bytes.
    """
    png_end = payload.find(_PNG_END)
    if png_end < 0:
        raise ValueError("PNG IEND marker not found")

    start = png_end + _PNG_END_TRAILER
    blobs = payload[start:]

    for offset in range(_TS_PACKET):
        positions = range(
            offset, min(len(blobs), offset + _TS_PACKET * sync_samples), _TS_PACKET
        )
        if positions and all(blobs[i] == _TS_SYNC for i in positions):
            return blobs[offset:]

    raise ValueError("TS sync 0x47 not found")


class KeyStore:
    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._cache: dict[str, bytes] = {}

    async def get(self, uri: str) -> bytes:
        if uri not in self._cache:
            self._cache[uri] = await self._http.get(uri)
        return self._cache[uri]


class SegmentFetcher:
    def __init__(self, http: HttpClient, key_store: KeyStore | None = None) -> None:
        self._http = http
        self._keys = key_store or KeyStore(http)

    async def fetch(self, segment: Segment) -> bytes:
        raw = await self._http.get(segment.uri)
        try:
            ts = unwrap_ts(raw)
        except ValueError:
            ts = raw

        if segment.key is None:
            return ts

        key = await self._keys.get(segment.key.uri)
        iv = segment.key.iv or segment.index.to_bytes(16, "big")
        return aes_128_cbc_decrypt(ts, key, iv)

    async def fetch_init(self, uri: str) -> bytes:
        """Descarga el segmento de inicialización (#EXT-X-MAP).

        El init (ftyp+moov) debe anteponerse a los fragmentos .m4s
        para obtener un MP4 válido.
        """
        raw = await self._http.get(uri)
        try:
            return unwrap_ts(raw)
        except ValueError:
            return raw

    async def size(self, uri: str) -> int | None:
        """Tamaño del recurso en bytes (best-effort, para la UI).

        Devuelve None si el cliente HTTP no lo soporta o el servidor
        no informa tamaño."""
        get_size = getattr(self._http, "size", None)
        if get_size is None:
            return None
        return await get_size(uri)
