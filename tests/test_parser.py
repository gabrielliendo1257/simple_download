from simple_downloader.engines.hls.models import AesKey
from simple_downloader.engines.hls.parser import parse_master, parse_media_playlist

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1280000,RESOLUTION=640x360,CODECS="avc1.4d401e"
360/video.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=5800000,RESOLUTION=1920x1080,CODECS="avc1.640028"
1080/video.m3u8
"""

MEDIA = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXT-X-KEY:METHOD=AES-128,URI="keys/key.bin",IV=0x000102030405060708090a0b0c0d0e0f
#EXTINF:6.0,
seg0.ts
#EXTINF:6.0,
seg1.ts
#EXT-X-ENDLIST
"""


def test_parse_master_returns_variants() -> None:
    variants = parse_master("https://cdn.example.com/master.m3u8", MASTER)

    assert len(variants) == 2
    assert variants[0].bandwidth == 1_280_000
    assert variants[0].resolution == "640x360"
    assert variants[1].resolution == "1920x1080"


def test_parse_master_resolves_relative_urls() -> None:
    variants = parse_master("https://cdn.example.com/master.m3u8", MASTER)

    assert variants[1].url == "https://cdn.example.com/1080/video.m3u8"


def test_parse_master_picks_no_max_without_bandwidth() -> None:
    variants = parse_master(
        "https://cdn.example.com/m.m3u8",
        "#EXTM3U\n#EXT-X-STREAM-INF:RESOLUTION=640x360\nlow.m3u8\n",
    )
    assert variants[0].bandwidth == 0


def test_parse_media_playlist_collects_segments() -> None:
    playlist = parse_media_playlist("https://cdn.example.com/1080/video.m3u8", MEDIA)

    assert len(playlist.segments) == 2
    assert playlist.target_duration == 6.0
    assert playlist.is_live is False
    assert playlist.segments[0].uri == "https://cdn.example.com/1080/seg0.ts"
    assert playlist.segments[1].index == 1


def test_parse_media_playlist_carries_key_to_all_segments() -> None:
    playlist = parse_media_playlist("https://cdn.example.com/1080/video.m3u8", MEDIA)

    expected = AesKey(
        method="AES-128",
        uri="https://cdn.example.com/1080/keys/key.bin",
        iv=bytes.fromhex("000102030405060708090a0b0c0d0e0f"),
    )
    assert playlist.segments[0].key == expected
    assert playlist.segments[1].key == expected


def test_parse_media_playlist_detects_live() -> None:
    live = MEDIA.replace("#EXT-X-ENDLIST\n", "")
    playlist = parse_media_playlist("https://cdn.example.com/live.m3u8", live)

    assert playlist.is_live is True


def test_parse_media_playlist_without_key() -> None:
    playlist = parse_media_playlist(
        "https://cdn.example.com/plain.m3u8",
        "#EXTM3U\n#EXTINF:6.0,\na.m4s\n#EXT-X-ENDLIST\n",
    )

    assert playlist.segments[0].key is None


def test_parse_media_playlist_ignores_comment_lines() -> None:
    playlist = parse_media_playlist(
        "https://cdn.example.com/plain.m3u8",
        "#EXTM3U\n#EXT-X-DISCONTINUITY\n#EXTINF:4.0,\nseg.ts\n#EXT-X-ENDLIST\n",
    )

    assert len(playlist.segments) == 1


def test_parse_media_playlist_extracts_init_map() -> None:
    fmp4 = """#EXTM3U
#EXT-X-MAP:URI="init-v1-a1.mp4"
#EXTINF:4.0,
seg-1-v1-a1.m4s
#EXTINF:4.0,
seg-2-v1-a1.m4s
#EXT-X-ENDLIST
"""
    playlist = parse_media_playlist("https://cdn.example.com/1080/index.m3u8", fmp4)

    assert playlist.init_uri == "https://cdn.example.com/1080/init-v1-a1.mp4"
    assert len(playlist.segments) == 2
    assert playlist.segments[0].uri.endswith("seg-1-v1-a1.m4s")


def test_parse_media_playlist_without_init_map() -> None:
    playlist = parse_media_playlist("https://cdn.example.com/1080/video.m3u8", MEDIA)

    assert playlist.init_uri is None