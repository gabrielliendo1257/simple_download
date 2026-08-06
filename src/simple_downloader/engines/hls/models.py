from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AesKey:
    method: str
    uri: str
    iv: bytes | None = None


@dataclass(frozen=True)
class Segment:
    index: int
    uri: str
    duration: float = 0.0
    key: AesKey | None = None


@dataclass(frozen=True)
class Variant:
    url: str
    bandwidth: int
    resolution: str | None = None
    codecs: str | None = None


@dataclass(frozen=True)
class HlsPlaylist:
    url: str
    segments: tuple[Segment, ...] = ()
    target_duration: float = 0.0
    is_live: bool = False
    resolution: str | None = None
    init_uri: str | None = None
