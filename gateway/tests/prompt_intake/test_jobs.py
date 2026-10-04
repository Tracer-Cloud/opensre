"""Tests for the prompt queue: bounded, ordered, and results kept for a while."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from config.constants.gateway import (
    PROMPT_CONVERSATION_NEW,
    PROMPT_JOB_STALE_SECONDS,
    PROMPT_PROGRESS_LINE_MAX_CHARS,
    PROMPT_PROGRESS_PLAN_MAX_CHARS,
    PROMPT_PROGRESS_PLAN_OMITTED,
)
from gateway.core.prompt_intake import (
    ALREADY_ANSWERED,
    ALREADY_SETTLED,
    ERROR_CANCELLED,
    ERROR_INTERRUPTED,
    ERROR_INVALID_ANSWER,
    NOT_WAITING,
    AnswerRefused,
    CancelRefused,
    JsonlPromptJobStore,
    PromptJob,
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


def test_a_turn_cut_off_by_a_restart_reads_as_interrupted_and_its_question_opens_again(
    tmp_path: Path,
) -> None:
    # Arrange: a question was answered and the answer's turn was running when the task died
    path = tmp_path / "prompt-jobs.jsonl"
    clock = _Clock()
    before = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    asked = before.submit("fix ci", context={}, actor="a")
    assert asked is not None
    before.take(timeout_seconds=0.01)
    asked.session_id = "s-1"
    before.needs_input(asked, "Which branch?", choice={"title": "Which branch?"})
    follow_up = before.answer(asked, "main")
    assert follow_up is not None and before.take(timeout_seconds=0.01) is follow_up

    # Act: a replacement task starts on the same file once the dead task's heartbeat is stale
    clock.now += PROMPT_JOB_STALE_SECONDS + 1.0
    after = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    cut_off = after.get(follow_up.id)
    parent = after.get(asked.id)
    assert cut_off is not None and parent is not None
    again = after.answer(parent, "release")

    # Assert: a terminal code instead of an unknown prompt; the question takes a new answer
    assert cut_off.view()["error"] == ERROR_INTERRUPTED
    assert cut_off.view()["parent_prompt_id"] == asked.id
    assert again is not None and again.session_id == "s-1"
    # Nothing is left to run: the dead turn is not replayed by the new task.
    assert after.take(timeout_seconds=0.01) is again
    assert after.take(timeout_seconds=0.01) is None
    # A third start reads the settled state, not the stale running record.
    third = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    reread = third.get(follow_up.id)
    assert reread is not None and reread.error_code == ERROR_INTERRUPTED


def test_expired_prompts_leave_the_store_while_running_and_at_restart(tmp_path: Path) -> None:
    # Arrange: two settled prompts, an hour's retention
    path = tmp_path / "prompt-jobs.jsonl"
    clock = _Clock()
    store = JsonlPromptJobStore(path)
    queue = PromptQueue(retention_seconds=60.0, clock=clock.read, store=store)
    early = queue.submit("early", context={}, actor="a")
    assert early is not None
    queue.finish(early, "done early")
    clock.now += 40.0
    late = queue.submit("late", context={}, actor="a")
    assert late is not None
    queue.finish(late, "done late")

    # Act: the first expires while the process runs; the second expires across a restart
    clock.now += 30.0
    queue.take_forgotten()
    stored_after_expiry = {record["id"] for record in store.load()}
    clock.now += 60.0
    restarted = PromptQueue(retention_seconds=60.0, clock=clock.read, store=store)

    # Assert
    assert stored_after_expiry == {late.id}
    assert restarted.get(late.id) is None
    assert store.load() == []


def _asked_and_answered(queue: PromptQueue, *, prompt: str) -> tuple[PromptJob, PromptJob]:
    """A prompt that stopped on a question, and the answer's follow-up now running."""
    asked = queue.submit(prompt, context={}, actor="a")
    assert asked is not None and queue.take(timeout_seconds=0.01) is asked
    asked.session_id = f"s-{prompt}"
    queue.needs_input(asked, "Which branch?", choice={"title": "Which branch?"})
    follow_up = queue.answer(asked, "main")
    assert follow_up is not None and queue.take(timeout_seconds=0.01) is follow_up
    return asked, follow_up


def test_a_task_starting_beside_a_live_one_leaves_its_running_answers_alone(
    tmp_path: Path,
) -> None:
    """An overlapping replacement must not reopen a question the old task is still answering."""
    # Arrange: the old task runs two answers; one will finish, the other's task will die
    path = tmp_path / "prompt-jobs.jsonl"
    clock = _Clock()
    old = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    _, finishing = _asked_and_answered(old, prompt="finishing")
    dying_parent, dying = _asked_and_answered(old, prompt="dying")

    # Act: the new task starts while the old one is alive
    clock.now += 5.0
    new = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path), refresh_seconds=0.0)
    parent_view = new.get(dying_parent.id)
    assert parent_view is not None
    with pytest.raises(AnswerRefused) as second_answer:
        new.answer(parent_view, "release")
    nothing_to_run = new.take(timeout_seconds=0.01)
    old.finish(finishing, "pushed to main")
    finished_view = new.get(finishing.id)
    clock.now += PROMPT_JOB_STALE_SECONDS - 10.0
    old.heartbeat()  # the old task is still running the dying answer
    clock.now += PROMPT_JOB_STALE_SECONDS - 10.0
    still_running = new.get(dying.id)
    assert still_running is not None and still_running.state is PromptState.RUNNING
    clock.now += 11.0  # past the window since the old task's last heartbeat
    silent = new.get(dying.id)

    # Assert: no second answer and no rerun while the owner lives; its outcome shows up here;
    # once it falls silent the answer is interrupted and the question takes an answer again
    assert second_answer.value.code == ALREADY_ANSWERED
    assert nothing_to_run is None
    assert finished_view is not None and finished_view.view()["answer"] == "pushed to main"
    assert silent is not None and silent.view()["error"] == ERROR_INTERRUPTED
    assert new.answer(parent_view, "release") is not None


def test_a_question_whose_answer_is_still_running_outlives_its_retention(tmp_path: Path) -> None:
    # Arrange: a one-minute retention; the question was asked 50 s before it was answered
    path = tmp_path / "prompt-jobs.jsonl"
    clock = _Clock()
    store = JsonlPromptJobStore(path)
    queue = PromptQueue(retention_seconds=60.0, clock=clock.read, store=store)
    asked = queue.submit("fix ci", context={}, actor="a")
    assert asked is not None and queue.take(timeout_seconds=0.01) is asked
    queue.needs_input(asked, "Which branch?")
    clock.now += 50.0
    follow_up = queue.answer(asked, "main")
    assert follow_up is not None and queue.take(timeout_seconds=0.01) is follow_up

    # Act: the question passes its retention while the answer runs, then the task dies
    clock.now += 20.0
    queue.take_forgotten()
    held_while_running = queue.get(asked.id)
    stored_while_running = {record["id"] for record in store.load()}
    clock.now += PROMPT_JOB_STALE_SECONDS + 1.0
    restarted = PromptQueue(retention_seconds=60.0, clock=clock.read, store=store)
    reopened = restarted.get(asked.id)

    # Assert: kept in memory and on disk, then reopened for a new answer after the restart
    assert held_while_running is asked
    assert asked.id in stored_while_running
    assert reopened is not None and restarted.answer(reopened, "release") is not None


def test_two_tasks_holding_one_question_accept_only_one_answer(tmp_path: Path) -> None:
    """Overlapping tasks must not both run an answer on the same conversation."""
    # Arrange: a waiting question, then two tasks that both loaded it from the shared store
    path = tmp_path / "prompt-jobs.jsonl"
    clock = _Clock()
    first = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    asked = first.submit("fix ci", context={}, actor="a")
    assert asked is not None and first.take(timeout_seconds=0.01) is asked
    first.needs_input(asked, "Which branch?", choice={"title": "Which branch?"})
    old_task = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    new_task = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    held_by_old = old_task.get(asked.id)
    held_by_new = new_task.get(asked.id)
    assert held_by_old is not None and held_by_new is not None

    # Act: each task is asked to answer the question it holds, with a different answer
    accepted = old_task.answer(held_by_old, "main")
    with pytest.raises(AnswerRefused) as refused:
        new_task.answer(held_by_new, "release")

    # Assert: one answer runs; the other task now shows the question answered by it
    assert accepted is not None and refused.value.code == ALREADY_ANSWERED
    assert new_task.take(timeout_seconds=0.01) is None
    assert held_by_new.answered_by == accepted.id
    seen_from_new = new_task.get(accepted.id)
    assert seen_from_new is not None and seen_from_new.view()["parent_prompt_id"] == asked.id


def test_a_resent_submission_or_answer_is_the_same_prompt_on_either_task(tmp_path: Path) -> None:
    """A caller that lost the response resends under its request id; nothing runs twice."""
    # Arrange: two tasks sharing the store during a replacement
    path = tmp_path / "prompt-jobs.jsonl"
    clock = _Clock()
    old = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    new = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))

    # Act: the submission reaches the old task, its resend the new one, then the old again
    first = old.submit("fix ci", context={}, actor="a", request_id="req-submit-1")
    resent_elsewhere = new.submit("fix ci", context={}, actor="a", request_id="req-submit-1")
    resent_here = old.submit("fix ci", context={}, actor="a", request_id="req-submit-1")
    assert first is not None and old.take(timeout_seconds=0.01) is first
    old.needs_input(first, "Which branch?")
    answered = old.answer(first, "main", request_id="req-answer-1")
    answered_again = old.answer(first, "main", request_id="req-answer-1")

    # Assert: one prompt and one follow-up; the new task runs nothing
    assert resent_elsewhere is not None and resent_elsewhere.id == first.id
    assert resent_here is first
    assert answered is not None and answered_again is answered
    assert new.take(timeout_seconds=0.01) is None
    assert old.take(timeout_seconds=0.01) is answered
    assert old.take(timeout_seconds=0.01) is None


def test_a_question_reopened_by_another_task_takes_an_answer_before_that_task_saves_it(
    tmp_path: Path,
) -> None:
    """A stale local claim must not refuse an answer the store would accept."""
    # Arrange: the old task's answer is running when the new task starts beside it
    path = tmp_path / "prompt-jobs.jsonl"
    clock = _Clock()
    old = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path))
    asked, rejected = _asked_and_answered(old, prompt="ci")
    new = PromptQueue(clock=clock.read, store=JsonlPromptJobStore(path), refresh_seconds=0.0)

    # Act: the old task fails the answer as unusable but has not saved the reopened question
    old.fail(rejected, ERROR_INVALID_ANSWER)
    seen_failed = new.get(rejected.id)
    parent = new.get(asked.id)
    assert parent is not None and parent.answered_by == rejected.id
    accepted = new.answer(parent, "release")
    with pytest.raises(AnswerRefused) as late:
        old.answer(asked, "main")

    # Assert: the new task takes the answer, and the store then refuses a second one
    assert seen_failed is not None and seen_failed.error_code == ERROR_INVALID_ANSWER
    assert accepted is not None and accepted.parent_id == asked.id
    assert late.value.code == ALREADY_ANSWERED


def test_one_conversation_runs_in_order_while_other_conversations_run_beside_it() -> None:
    # Arrange: two prompts on alice's own conversation, one on bob's, one new conversation
    queue = PromptQueue(clock=_Clock().read)
    first = queue.submit("first", context={}, actor="alice")
    second = queue.submit("second", context={}, actor="alice")
    other_actor = queue.submit("other actor", context={}, actor="bob")
    separate = queue.submit(
        "separate", context={}, actor="alice", conversation=PROMPT_CONVERSATION_NEW
    )
    assert first and second and other_actor and separate

    # Act
    taken = [queue.take(timeout_seconds=0.01) for _ in range(4)]
    waiting = second.state
    queue.finish(first, "done")
    after_first = queue.take(timeout_seconds=0.01)

    # Assert: alice's second prompt waits for her first; nothing else waits for anything
    assert taken == [first, other_actor, separate, None]
    assert waiting is PromptState.QUEUED
    assert after_first is second


def test_a_prompt_whose_session_is_busy_waits_first_in_line_and_keeps_its_conversation() -> None:
    # Arrange: a prompt that named alice's conversation runs on it
    queue = PromptQueue(clock=_Clock().read)
    named = queue.submit("named", context={}, actor="alice", conversation=_SESSION)
    own = queue.submit("own", context={}, actor="alice")
    later = queue.submit("later", context={}, actor="alice")
    assert named and own and later
    assert queue.take(timeout_seconds=0.01) is named
    assert queue.take(timeout_seconds=0.01) is own

    # Act: alice's own conversation resolves to the session the named prompt holds
    bound = queue.bind_session(own, _SESSION)
    queue.defer(own, _SESSION)
    while_named_runs = queue.take(timeout_seconds=0.01)
    queue.finish(named, "done")
    after_named = queue.take(timeout_seconds=0.01)

    # Assert: it neither ran beside the named prompt nor let the later prompt pass it
    assert bound is False and own.session_id == ""
    assert while_named_runs is None and later.state is PromptState.QUEUED
    assert after_named is own and queue.bind_session(own, _SESSION)
    assert own.session_id == _SESSION


def test_a_prompt_continuing_the_actors_conversation_keeps_its_place_before_one_naming_it() -> None:
    # Arrange: alice's own conversation is _SESSION; she continues it, then names it
    queue = PromptQueue(clock=_Clock().read, hosted_conversation={"alice": _SESSION}.get)
    continuing = queue.submit("continue", context={}, actor="alice")
    naming = queue.submit("named", context={}, actor="alice", conversation=_SESSION)
    assert continuing is not None and naming is not None

    # Act
    first = queue.take(timeout_seconds=0.01)
    while_first_runs = queue.take(timeout_seconds=0.01)
    queue.finish(continuing, "done")
    second = queue.take(timeout_seconds=0.01)

    # Assert: they never ran together, and in the order they came
    assert first is continuing and while_first_runs is None
    assert second is naming


def test_once_a_prompt_resolves_the_actors_conversation_later_ones_queue_behind_it() -> None:
    # Arrange: nothing told the queue alice's conversation before her first prompt ran
    queue = PromptQueue(clock=_Clock().read)
    first = queue.submit("first", context={}, actor="alice")
    assert first is not None and queue.take(timeout_seconds=0.01) is first
    assert queue.bind_session(first, _SESSION, hosted=True)
    continuing = queue.submit("continue", context={}, actor="alice")
    naming = queue.submit("named", context={}, actor="alice", conversation=_SESSION)
    assert continuing is not None and naming is not None

    # Act
    while_first_runs = queue.take(timeout_seconds=0.01)
    queue.finish(first, "done")
    taken = [queue.take(timeout_seconds=0.01), queue.take(timeout_seconds=0.01)]

    # Assert: both wait for the session, then run one at a time in submission order
    assert while_first_runs is None
    assert taken == [continuing, None]


def test_cancel_settles_a_queued_prompt_stops_a_running_one_and_refuses_a_settled_one() -> None:
    # Arrange
    queue = PromptQueue(clock=_Clock().read)
    running = queue.submit("running", context={}, actor="a")
    queued = queue.submit("queued", context={}, actor="a")
    assert running is not None and queued is not None
    assert queue.take(timeout_seconds=0.01) is running
    turn_cancel = threading.Event()
    queue.attach_cancel(running, turn_cancel)

    # Act
    queue.cancel(queued)
    queue.cancel(running)
    with pytest.raises(CancelRefused) as settled:
        queue.cancel(queued)

    # Assert: the queued prompt is gone from the queue; the running turn is told to stop
    assert (queued.state, queued.error_code) == (PromptState.FAILED, ERROR_CANCELLED)
    assert queue.queued_count() == 0
    assert turn_cancel.is_set() and running.state is PromptState.RUNNING
    assert running.view()["cancel_requested"] is True
    assert settled.value.code == ALREADY_SETTLED


def test_a_cancel_before_the_turn_starts_reaches_the_turn_and_a_cancelled_answer_reopens() -> None:
    # Arrange: a prompt taken but not yet started, and a question with a queued answer
    queue = PromptQueue(clock=_Clock().read)
    starting = queue.submit("starting", context={}, actor="a")
    asked = queue.submit("asked", context={}, actor="b")
    assert starting is not None and asked is not None
    assert queue.take(timeout_seconds=0.01) is starting
    asked.session_id = _SESSION
    queue.needs_input(asked, "Which branch?")
    answer = queue.answer(asked, "main")
    assert answer is not None

    # Act
    queue.cancel(starting)
    turn_cancel = threading.Event()
    queue.attach_cancel(starting, turn_cancel)
    queue.cancel(answer)
    again = queue.answer(asked, "release")

    # Assert
    assert turn_cancel.is_set()
    assert answer.error_code == ERROR_CANCELLED
    assert again is not None and asked.answered_by == again.id


_SESSION = "0b6f2c1e-8d4a-4c55-9a77-2f1c3e5d7a90"
