from __future__ import annotations

from enum import Enum, auto

from simple_downloader.domain.protocols import HttpClient

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_TS_SYNC = 0x47
_TS_PACKET = 188
_PROBE_BYTES = 8192


class SegmentFormat(Enum):
    TS = auto()
    FMP4 = auto()
    PNG_WRAPPED = auto()
    UNKNOWN = auto()


def sniff_segment(raw: bytes) -> SegmentFormat:
    """Clasifica un segmento por sus primeros bytes.

    - PNG_WRAPPED: el segmento real va dentro de un PNG (sitios que
      ofuscan el stream: follame.top).
    - TS: sync byte 0x47 validado con estride de 188 bytes.
    - FMP4: cajas mp4 (ftyp/moof/styp) -> segmentos fragmentados .m4s.
    """
    if raw.startswith(_PNG_MAGIC):
        return SegmentFormat.PNG_WRAPPED

    box = raw[4:8]
    if box in (b"ftyp", b"moof", b"styp", b"sidx"):
        return SegmentFormat.FMP4

    if len(raw) >= _TS_PACKET and raw[0] == _TS_SYNC and raw[_TS_PACKET] == _TS_SYNC:
        return SegmentFormat.TS

    return SegmentFormat.UNKNOWN


async def probe_segment(http: HttpClient, url: str) -> SegmentFormat:
    """Descarga solo el inicio del primer segmento y lo clasifica.

    Usa Range (8KB) si el cliente lo soporta; si no, baja el segmento
    completo. El objetivo es decidir si el stream requiere desempaque
    propio o puede delegarse a yt-dlp.
    """
    get_range = getattr(http, "get_range", None)
    if get_range is not None:
        raw = await get_range(url, 0, _PROBE_BYTES - 1)
    else:
        raw = await http.get(url)
    return sniff_segment(raw)
