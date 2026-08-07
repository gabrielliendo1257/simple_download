import json
import sqlite3
from pathlib import Path
from uuid import UUID

from simple_downloader.domain.models import (
    DownloadContext,
    DownloadJob,
    DownloadOutput,
    DownloadProgress,
    DownloadRequest,
)
from simple_downloader.domain.protocols import DownloadJobRepository
from simple_downloader.domain.state import DownloadState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id         TEXT PRIMARY KEY,
    payload    TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class InMemoryRepository(DownloadJobRepository):
    def __init__(self) -> None:
        self._jobs: dict[UUID, DownloadJob] = {}

    async def save(self, job: DownloadJob) -> None:
        self._jobs[job.id] = job

    async def find(self, id: UUID) -> DownloadJob | None:
        return self._jobs.get(id)

    async def list(self) -> list[DownloadJob]:
        return list(self._jobs.values())

    async def delete(self, job_id: UUID) -> None:
        self._jobs.pop(job_id, None)


class SqliteRepository(DownloadJobRepository):
    """Catálogo persistente de jobs en SQLite.

    Guarda la descarga *tal como fue pedida* (URL, título, salida, estado)
    y su progreso/error/aviso. El `task` no se persiste: al reanudar se
    reconstruye con `DownloadManager.resume` y el parcial en disco decide
    el offset real (el disco es la fuente de verdad de los bytes).
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    async def save(self, job: DownloadJob) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO jobs (id, payload, updated_at) VALUES (?, ?, ?)",
            (str(job.id), _encode(job), _now()),
        )
        self._conn.commit()

    async def find(self, job_id: UUID) -> DownloadJob | None:
        row = self._conn.execute(
            "SELECT payload FROM jobs WHERE id = ?", (str(job_id),)
        ).fetchone()
        if row is None:
            return None
        return _decode(row[0])

    async def list(self) -> list[DownloadJob]:
        rows = self._conn.execute(
            "SELECT payload FROM jobs ORDER BY updated_at"
        ).fetchall()
        return [_decode(row[0]) for row in rows]

    async def delete(self, job_id: UUID) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (str(job_id),))
        self._conn.commit()


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _encode(job: DownloadJob) -> str:
    request = job.request
    output = request.output
    context = request.context
    progress = job.progress

    payload = {
        "id": str(job.id),
        "request": {
            "url": request.url,
            "title": request.title,
            "output": (
                {
                    "directory": str(output.directory),
                    "filename": output.filename,
                    "template": output.template,
                    "overwrite": output.overwrite,
                    "create_directories": output.create_directories,
                }
                if output is not None
                else None
            ),
            "context": (
                {
                    "referer": context.referer,
                    "user_agent": context.user_agent,
                    "headers": context.headers,
                    "timeout_sec": context.timeout_sec,
                    "max_parallel_segments": context.max_parallel_segments,
                }
                if context is not None
                else None
            ),
            "format_id": request.format_id,
            "extract_audio": request.extract_audio,
            "audio_format": request.audio_format,
            "subtitles": request.subtitles,
            "resume": request.resume,
        },
        "state": job.state.name,
        "progress": (
            {
                "downloaded_bytes": progress.downloaded_bytes,
                "total_bytes": progress.total_bytes,
                "speed_bps": progress.speed_bps,
                "segments_done": progress.segments_done,
                "segments_total": progress.segments_total,
            }
            if progress is not None
            else None
        ),
        "engine": job.engine,
        "error": job.error,
        "notice": job.notice,
    }
    return json.dumps(payload)


def _decode(raw: str) -> DownloadJob:
    payload = json.loads(raw)
    request_data = payload["request"]

    output_data = request_data.get("output")
    output = (
        DownloadOutput(
            directory=Path(output_data["directory"]),
            filename=output_data.get("filename"),
            template=output_data.get("template"),
            overwrite=bool(output_data.get("overwrite", False)),
            create_directories=bool(output_data.get("create_directories", True)),
        )
        if output_data is not None
        else None
    )

    context_data = request_data.get("context")
    context = (
        DownloadContext(
            referer=context_data.get("referer"),
            user_agent=context_data.get("user_agent"),
            headers=dict(context_data.get("headers") or {}),
            timeout_sec=float(context_data.get("timeout_sec", 30.0)),
            max_parallel_segments=int(context_data.get("max_parallel_segments", 6)),
        )
        if context_data is not None
        else None
    )

    request = DownloadRequest(
        url=request_data["url"],
        title=request_data.get("title"),
        output=output,
        context=context,
        format_id=request_data.get("format_id"),
        extract_audio=bool(request_data.get("extract_audio", False)),
        audio_format=request_data.get("audio_format"),
        subtitles=bool(request_data.get("subtitles", False)),
        resume=bool(request_data.get("resume", False)),
    )

    progress_data = payload.get("progress")
    progress = (
        DownloadProgress(
            downloaded_bytes=progress_data["downloaded_bytes"],
            total_bytes=progress_data.get("total_bytes"),
            speed_bps=progress_data.get("speed_bps"),
            segments_done=progress_data.get("segments_done"),
            segments_total=progress_data.get("segments_total"),
        )
        if progress_data is not None
        else None
    )

    return DownloadJob(
        id=UUID(payload["id"]),
        request=request,
        state=DownloadState[payload["state"]],
        progress=progress,
        engine=payload.get("engine"),
        error=payload.get("error"),
        notice=payload.get("notice"),
    )
