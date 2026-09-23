"""A scheduler counts as hosted only after a long-lived host says so."""

from __future__ import annotations

import threading

import pytest

from infrastructure.scheduling.scheduler import runner


def test_scheduler_is_not_hosted_until_a_host_marks_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a fresh flag, as in a process that has not started a host
    monkeypatch.setattr(runner, "_HOSTED_IN_PROCESS", threading.Event())
    assert not runner.scheduler_hosted_in_process()

    # Act
    runner.mark_scheduler_hosted()

    # Assert
    assert runner.scheduler_hosted_in_process()
