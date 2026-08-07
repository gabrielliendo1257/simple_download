from pathlib import Path
from uuid import uuid4

from simple_downloader.db import InMemoryRepository, SqliteRepository
from simple_downloader.domain.models import (
    DownloadContext,
    DownloadJob,
    DownloadOutput,
    DownloadProgress,
    DownloadRequest,
)
from simple_downloader.domain.state import DownloadState


def _job() -> DownloadJob:
    return DownloadJob(
        id=uuid4(),
        request=DownloadRequest(
            url="https://x/file.mp4",
            title="file.mp4",
            output=DownloadOutput(directory=Path("/tmp/dl"), filename="file.mp4"),
            context=DownloadContext(
                referer="https://x/",
                user_agent="test-agent",
                headers={"cookie": "a=1"},
                cookies_path="/tmp/cookies.txt",
            ),
            format_id="720",
            extract_audio=True,
            audio_format="mp3",
            subtitles=True,
            resume=True,
        ),
        state=DownloadState.PAUSED,
        progress=DownloadProgress(downloaded_bytes=2048, total_bytes=4096),
        engine="http",
        error=None,
        notice="el servidor ignoró el rango (HTTP 200); se reinició la descarga",
    )


async def test_sqlite_roundtrip(tmp_path) -> None:
    repo = SqliteRepository(tmp_path / "jobs.db")
    job = _job()

    await repo.save(job)
    loaded = await repo.find(job.id)

    assert loaded is not None
    assert loaded.id == job.id
    assert loaded.request == job.request
    assert loaded.state is DownloadState.PAUSED
    assert loaded.progress == job.progress
    assert loaded.engine == "http"
    assert loaded.notice == job.notice
    assert loaded.task is None


async def test_sqlite_find_missing(tmp_path) -> None:
    repo = SqliteRepository(tmp_path / "jobs.db")
    assert await repo.find(uuid4()) is None


async def test_sqlite_list_keeps_order(tmp_path) -> None:
    repo = SqliteRepository(tmp_path / "jobs.db")
    first, second = _job(), _job()
    await repo.save(first)
    await repo.save(second)

    jobs = await repo.list()
    assert [j.id for j in jobs] == [first.id, second.id]


async def test_sqlite_delete(tmp_path) -> None:
    repo = SqliteRepository(tmp_path / "jobs.db")
    job = _job()
    await repo.save(job)
    await repo.delete(job.id)

    assert await repo.find(job.id) is None
    assert await repo.list() == []


async def test_sqlite_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    job = _job()

    first = SqliteRepository(path)
    await first.save(job)
    first.close()

    second = SqliteRepository(path)
    loaded = await second.find(job.id)
    assert loaded is not None
    assert loaded.request == job.request
    assert loaded.state is DownloadState.PAUSED


async def test_sqlite_serializes_terminal_states(tmp_path) -> None:
    repo = SqliteRepository(tmp_path / "jobs.db")
    job = DownloadJob(
        id=uuid4(),
        request=DownloadRequest(url="https://x/playlist.m3u8"),
        state=DownloadState.FAILED,
        engine="hls",
        error="segment 3 failed to download",
    )

    await repo.save(job)
    loaded = await repo.find(job.id)

    assert loaded is not None
    assert loaded.state is DownloadState.FAILED
    assert loaded.error == "segment 3 failed to download"
    assert loaded.request.url == "https://x/playlist.m3u8"


async def test_inmemory_delete() -> None:
    repo = InMemoryRepository()
    job = _job()
    await repo.save(job)
    await repo.delete(job.id)
    assert await repo.find(job.id) is None
