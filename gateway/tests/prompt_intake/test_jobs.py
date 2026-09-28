"""Tests for the prompt queue: bounded, ordered, and results kept for a while."""

from __future__ import annotations

import pytest

from gateway.core.prompt_intake import (
    ALREADY_ANSWERED,
    NOT_WAITING,
    AnswerRefused,
    PromptQueue,
    PromptState,
)


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

    # Assert: the record is gone for callers and handed to whoever retires its session
    assert view_before is not None and view_before.view() == {
        "prompt_id": job.id,
        "state": "done",
        "answer": "three scheduled tasks",
        "finished_at": 1_000.0,
    }
    assert view_after is None
    assert queue.take_forgotten() == [job] and queue.take_forgotten() == []


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


def test_an_answer_becomes_a_follow_up_on_the_parents_session_and_only_once() -> None:
    # Arrange: a prompt that stopped to ask
    queue = PromptQueue(clock=_Clock())
    parent = queue.submit("fix ci", context={}, actor="a")
    assert parent is not None
    queue.take(timeout_seconds=0.01)
    parent.session_id = "s-1"
    queue.needs_input(parent, "Which branch?", choice={"title": "Which branch?"})

    # Act
    follow_up = queue.answer(parent, "main")
    with pytest.raises(AnswerRefused) as second:
        queue.answer(parent, "release")

    # Assert: the follow-up carries the answer on the same session; the parent is answered once
    assert follow_up is not None
    assert (follow_up.prompt, follow_up.session_id, follow_up.parent_id) == (
        "main",
        "s-1",
        parent.id,
    )
    assert (
        follow_up.view()["parent_prompt_id"] == parent.id
        and "parent_prompt_id" not in parent.view()
    )
    assert follow_up.actor == "a" and follow_up.state is PromptState.QUEUED
    assert parent.answered_by == follow_up.id
    assert second.value.code == ALREADY_ANSWERED
    assert queue.take(timeout_seconds=0.01) is follow_up


def test_a_prompt_that_is_not_asking_refuses_an_answer_and_a_reopened_one_takes_another() -> None:
    # Arrange
    queue = PromptQueue(clock=_Clock())
    done = queue.submit("a", context={}, actor="a")
    asked = queue.submit("b", context={}, actor="a")
    assert done is not None and asked is not None
    queue.finish(done, "answered")
    queue.needs_input(asked, "Which?")
    first = queue.answer(asked, "1")

    # Act
    with pytest.raises(AnswerRefused) as refused:
        queue.answer(done, "1")
    queue.reopen(asked.id)
    second = queue.answer(asked, "2")

    # Assert
    assert refused.value.code == NOT_WAITING
    assert first is not None and second is not None and second.id != first.id


def test_progress_keeps_the_newest_lines_with_growing_indices() -> None:
    # Arrange
    queue = PromptQueue(clock=_Clock())
    job = queue.submit("fix ci", context={}, actor="a")
    assert job is not None
    job.progress = __import__("collections").deque(maxlen=2)

    # Act
    queue.note(job, "  Reading the workflow run  ")
    queue.note(job, "")
    queue.note(job, "Checking out the branch")
    queue.note(job, "x" * 500)

    # Assert: blank lines are dropped, long lines cut, only the newest kept, indices keep growing
    progress = job.view()["progress"]
    assert [item["index"] for item in progress] == [1, 2]
    assert progress[0]["text"] == "Checking out the branch"
    assert len(progress[1]["text"]) == 200
