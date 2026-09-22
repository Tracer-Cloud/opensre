"""Tests for the prompt queue: bounded, ordered, and results kept for a while."""

from __future__ import annotations

from gateway.core.prompt_intake import PromptQueue, PromptState


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_a_full_queue_refuses_and_a_taken_job_is_running() -> None:
    # Arrange
    queue = PromptQueue(max_queued=1, clock=_Clock())

    # Act
    first = queue.submit("first", context={}, actor="a")
    second = queue.submit("second", context={}, actor="a")
    taken = queue.take(timeout_seconds=0.01)
    third = queue.submit("third", context={}, actor="a")

    # Assert: only queued jobs count toward the limit, and taking marks the job running.
    assert first is not None and second is None and third is not None
    assert taken is first and taken.state is PromptState.RUNNING
    assert queue.get(first.id) is first


def test_a_settled_result_is_forgotten_after_the_retention_window() -> None:
    # Arrange
    clock = _Clock()
    queue = PromptQueue(retention_seconds=60.0, clock=clock)
    job = queue.submit("what runs here?", context={"repository": "o/r"}, actor="a")
    assert job is not None
    queue.take(timeout_seconds=0.01)
    queue.finish(job, "three scheduled tasks")

    # Act
    view_before = queue.get(job.id)
    clock.now += 61.0
    view_after = queue.get(job.id)

    # Assert
    assert view_before is not None and view_before.view() == {
        "prompt_id": job.id,
        "state": "done",
        "answer": "three scheduled tasks",
        "finished_at": 1_000.0,
    }
    assert view_after is None


def test_the_view_shows_only_the_field_for_its_state() -> None:
    # Arrange
    queue = PromptQueue(clock=_Clock())
    asked = queue.submit("a", context={}, actor="a")
    failed = queue.submit("b", context={}, actor="a")
    assert asked is not None and failed is not None

    # Act
    queue.needs_input(asked, "Which branch? (main, release)")
    queue.fail(failed, "turn_failed")

    # Assert
    assert (
        asked.view()["question"] == "Which branch? (main, release)" and "answer" not in asked.view()
    )
    assert failed.view()["error"] == "turn_failed" and "answer" not in failed.view()
