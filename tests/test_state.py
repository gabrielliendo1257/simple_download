from simple_downloader.domain.state import DownloadState, can_transition


def test_allowed_transitions() -> None:
    assert can_transition(DownloadState.QUEUED, DownloadState.RUNNING)
    assert can_transition(DownloadState.QUEUED, DownloadState.CANCELLED)
    assert can_transition(DownloadState.RUNNING, DownloadState.PAUSED)
    assert can_transition(DownloadState.RUNNING, DownloadState.COMPLETED)
    assert can_transition(DownloadState.RUNNING, DownloadState.FAILED)
    assert can_transition(DownloadState.PAUSED, DownloadState.RUNNING)


def test_forbidden_transitions() -> None:
    assert not can_transition(DownloadState.QUEUED, DownloadState.COMPLETED)
    assert not can_transition(DownloadState.QUEUED, DownloadState.PAUSED)
    assert not can_transition(DownloadState.COMPLETED, DownloadState.RUNNING)
    assert not can_transition(DownloadState.FAILED, DownloadState.RUNNING)
    assert not can_transition(DownloadState.CANCELLED, DownloadState.RUNNING)


def test_terminal_states_are_dead_ends() -> None:
    for terminal in (DownloadState.COMPLETED, DownloadState.FAILED, DownloadState.CANCELLED):
        assert not can_transition(terminal, DownloadState.RUNNING)
        assert not can_transition(terminal, DownloadState.QUEUED)