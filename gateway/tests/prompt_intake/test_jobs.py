"""Tests for the prompt queue: bounded, ordered, and results kept for a while."""

from __future__ import annotations

import pytest

from config.constants.gateway import (
    PROMPT_PROGRESS_LINE_MAX_CHARS,
    PROMPT_PROGRESS_PLAN_MAX_CHARS,
    PROMPT_PROGRESS_PLAN_OMITTED,
)
from gateway.core.prompt_intake import (
    ALREADY_ANSWERED,
    NOT_WAITING,
    AnswerRefused,
    PromptQueue,
    PromptState,
)


class _Clock:
    """Manual clock. The queue calls ``read``."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def read(self) -> float:
        return self.now


def test_a_full_queue_refuses_and_a_taken_job_is_running() -> None:
    # Arrange
    queue = PromptQueue(max_queued=1, clock=_Clock().read)

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
    queue = PromptQueue(retention_seconds=60.0, clock=clock.read)
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
    queue = PromptQueue(clock=_Clock().read)
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
    queue = PromptQueue(clock=_Clock().read)
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
    queue = PromptQueue(clock=_Clock().read)
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
    queue = PromptQueue(clock=_Clock().read)
    job = queue.submit("fix ci", context={}, actor="a")
    assert job is not None
    job.progress = __import__("collections").deque(maxlen=2)

    # Act
    queue.note(job, "  Reading the workflow run  ")
    queue.note(job, "")
    queue.note(job, "Checking out the branch")
    queue.note(job, "x" * (PROMPT_PROGRESS_LINE_MAX_CHARS + 80))

    # Assert: blank lines are dropped, long lines cut, only the newest kept, indices keep growing
    progress = job.view()["progress"]
    assert [item["index"] for item in progress] == [1, 2]
    assert progress[0]["text"] == "Checking out the branch"
    assert len(progress[1]["text"]) == PROMPT_PROGRESS_LINE_MAX_CHARS


def test_a_repeated_plan_is_not_recorded_again_and_a_tool_keeps_its_kind() -> None:
    queue = PromptQueue(clock=_Clock().read)
    job = queue.submit("fix ci", context={}, actor="a")
    assert job is not None
    checklist = "Plan · 1/2\n  ● List orgs\n  ○ Check permission"

    queue.note(job, "GitHub CLI · gh api user", kind="tool")
    queue.note(job, checklist, kind="plan")
    queue.note(job, checklist, kind="plan")
    queue.note(job, "Plan complete · 2/2\n  ✓ List orgs", kind="plan_done")

    progress = job.view()["progress"]
    assert [item["kind"] for item in progress] == ["tool", "plan", "plan_done"]
    assert progress[0]["text"] == "GitHub CLI · gh api user"
    assert "{'step'" not in progress[1]["text"]


def test_a_plan_longer_than_a_status_line_keeps_every_step() -> None:
    """The three-row status budget must not slice a hosted checklist mid-step."""
    queue = PromptQueue(clock=_Clock().read)
    job = queue.submit("fix ci", context={}, actor="a")
    assert job is not None
    steps = [
        f"  ○ Confirm step {index}: record the hosted repair evidence before continuing"
        for index in range(12)
    ]
    checklist = "Plan · 1/12\n  ● Start the repair\n" + "\n".join(steps)
    assert len(checklist) > PROMPT_PROGRESS_LINE_MAX_CHARS
    assert len(checklist) < PROMPT_PROGRESS_PLAN_MAX_CHARS

    queue.note(job, checklist, kind="plan")

    assert job.view()["progress"][0]["text"] == checklist


def test_an_oversized_plan_drops_whole_steps_and_says_so() -> None:
    queue = PromptQueue(clock=_Clock().read)
    job = queue.submit("fix ci", context={}, actor="a")
    assert job is not None
    step = "  ○ " + ("confirm the repair outcome." * 200)
    checklist = "\n".join(["Plan · 1/3", step, step, step])
    assert len(checklist) > PROMPT_PROGRESS_PLAN_MAX_CHARS

    queue.note(job, checklist, kind="plan")

    lines = job.view()["progress"][0]["text"].splitlines()
    assert lines[0] == "Plan · 1/3"
    assert lines[-1] == PROMPT_PROGRESS_PLAN_OMITTED
    assert lines[1:-1]
    assert all(line == step for line in lines[1:-1])
    assert len(lines) < 5


def test_progress_keeps_a_three_row_status() -> None:
    """A three-row gateway status is stored whole."""
    queue = PromptQueue(clock=_Clock().read)
    job = queue.submit("fix ci", context={}, actor="a")
    assert job is not None
    text = "\n".join(
        [
            "⏳ Load the full body of one action-agent skill by name from the SKILLS INDEX…",
            "(operating-github-ci-repairs)",
            "(rg -n -C 3 'GET /repos/davincios/opensre-onboarding-ci-repair-demo')",
        ]
    )

    queue.note(job, f"  {text}  ")

    assert job.view()["progress"][0]["text"] == text
