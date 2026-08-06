from __future__ import annotations

import re
from urllib.parse import urljoin

from simple_downloader.engines.hls.models import AesKey, HlsPlaylist, Segment, Variant


def parse_master(master_url: str, text: str) -> tuple[Variant, ...]:
    variants: list[Variant] = []
    lines = text.splitlines()

    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue

        resolution = re.search(r"RESOLUTION=([0-9]+x[0-9]+)", line)
        bandwidth = re.search(r"BANDWIDTH=([0-9]+)", line)
        codecs = re.search(r'CODECS="([^"]+)"', line)
        uri = lines[index + 1].strip() if index + 1 < len(lines) else ""

        if uri:
            variants.append(
                Variant(
                    url=urljoin(master_url, uri),
                    resolution=resolution.group(1) if resolution else None,
                    bandwidth=int(bandwidth.group(1)) if bandwidth else 0,
                    codecs=codecs.group(1) if codecs else None,
                )
            )

    return tuple(variants)


def parse_media_playlist(
    playlist_url: str, text: str, resolution: str | None = None
) -> HlsPlaylist:
    target_duration = re.search(r"#EXT-X-TARGETDURATION:([0-9.]+)", text)
    is_live = "#EXT-X-ENDLIST" not in text
    init_map = re.search(r'#EXT-X-MAP:URI="([^"]+)"', text)

    segments: list[Segment] = []
    current_key: AesKey | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if line.startswith("#EXT-X-KEY"):
            method = re.search(r"METHOD=([^,;]+)", line)
            uri = re.search(r'URI="([^"]+)"', line)
            iv = re.search(r"IV=0x([0-9a-fA-F]+)", line)

            if method is None or uri is None:
                continue

            current_key = AesKey(
                method=method.group(1),
                uri=urljoin(playlist_url, uri.group(1)),
                iv=bytes.fromhex(iv.group(1)) if iv else None,
            )
        elif line.startswith("#EXT-X-MAP") or line.startswith("#"):
            continue
        elif line:
            segments.append(
                Segment(
                    index=len(segments),
                    uri=urljoin(playlist_url, line),
                    key=current_key,
                )
            )

    return HlsPlaylist(
        url=playlist_url,
        segments=tuple(segments),
        target_duration=float(target_duration.group(1)) if target_duration else 0.0,
        is_live=is_live,
        resolution=resolution,
        init_uri=urljoin(playlist_url, init_map.group(1)) if init_map else None,
    )
