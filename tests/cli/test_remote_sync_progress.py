"""``CliProgress`` — Click progress-bar rendering for ``opensre remote-sync sync``."""

from __future__ import annotations

from infrastructure.filestorage.engine import SyncProgress
from infrastructure.filestorage.enums import SyncDirection
from surfaces.cli.commands.remote_sync_progress import CliProgress


def test_first_event_opens_a_bar_with_the_right_length_and_label() -> None:
    progress = CliProgress()

    progress(SyncProgress(SyncDirection.PULL, "sessions/a.jsonl", 1, 3))

    assert progress._bar is not None
    assert progress._bar.length == 3
    assert progress._bar.label == "Pulling"
    assert progress._bar.pos == 1
    progress.close()


def test_a_direction_change_closes_the_old_bar_and_opens_a_new_one() -> None:
    progress = CliProgress()
    progress(SyncProgress(SyncDirection.PULL, "sessions/a.jsonl", 1, 1))
    first_bar = progress._bar

    progress(SyncProgress(SyncDirection.PUSH, "sessions/b.jsonl", 1, 2))

    assert progress._bar is not first_bar
    assert progress._bar.length == 2
    assert progress._bar.label == "Pushing"
    progress.close()


def test_same_direction_reuses_the_bar_and_advances_it() -> None:
    progress = CliProgress()
    progress(SyncProgress(SyncDirection.PUSH, "sessions/a.jsonl", 1, 2))
    first_bar = progress._bar

    progress(SyncProgress(SyncDirection.PUSH, "sessions/b.jsonl", 2, 2))

    assert progress._bar is first_bar
    assert progress._bar.pos == 2
    progress.close()


def test_close_is_idempotent() -> None:
    progress = CliProgress()
    progress(SyncProgress(SyncDirection.PULL, "sessions/a.jsonl", 1, 1))

    progress.close()
    progress.close()  # must not raise

    assert progress._bar is None


def test_current_item_is_sanitized_before_reaching_the_bar() -> None:
    progress = CliProgress()

    progress(SyncProgress(SyncDirection.PULL, "sessions/\x1b[2Kpwned.jsonl", 1, 1))

    assert "\x1b" not in progress._bar.current_item
    progress.close()
