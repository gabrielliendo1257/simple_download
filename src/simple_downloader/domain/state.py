from __future__ import annotations

from enum import Enum, auto


class DownloadState(Enum):
    QUEUED = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


_ALLOWED_TRANSITIONS: dict[DownloadState, frozenset[DownloadState]] = {
    DownloadState.QUEUED: frozenset(
        {DownloadState.RUNNING, DownloadState.CANCELLED, DownloadState.FAILED}
    ),
    DownloadState.RUNNING: frozenset(
        {
            DownloadState.PAUSED,
            DownloadState.COMPLETED,
            DownloadState.FAILED,
            DownloadState.CANCELLED,
        }
    ),
    DownloadState.PAUSED: frozenset(
        {
            DownloadState.RUNNING,
            DownloadState.COMPLETED,
            DownloadState.CANCELLED,
            DownloadState.FAILED,
        }
    ),
    DownloadState.COMPLETED: frozenset(),
    DownloadState.FAILED: frozenset(),
    DownloadState.CANCELLED: frozenset(),
}


def can_transition(current: DownloadState, next_state: DownloadState) -> bool:
    return next_state in _ALLOWED_TRANSITIONS[current]
