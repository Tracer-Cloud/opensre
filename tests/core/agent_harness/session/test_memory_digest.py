"""The extraction digest built from a real session log.

The log is written with the production :class:`JsonlSessionStore` in the order
a turn writes it — tool records while the turn runs (WAL sidecars), then the
user and assistant messages when it ends — so the digest is tested against
the format it actually reads.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_harness.session.memory_digest import (
    MAX_TOOL_RESULT_CHARS,
    build_session_digest,
)
from core.agent_harness.session.memory_turns import DemoTurns
from core.agent_harness.session.persistence.contracts import CARRIED_MESSAGE_METADATA_KEY
from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
from core.agent_harness.session.session_core import SessionCore
from core.state.transcript_window import SESSION_SUMMARY_PREFIX

_NO_DEMO = DemoTurns()


@pytest.fixture
def log() -> tuple[JsonlSessionStore, str]:
    store = JsonlSessionStore()
    session = SessionCore(session_id="digest-session")
    store.open_session(session)
    return store, session.session_id


def _tool(
    log: tuple[JsonlSessionStore, str], tool: str, arguments: dict[str, Any], result: str
) -> None:
    store, session_id = log
    store.append_tool_call(
        session_id,
        tool=tool,
        arguments=arguments,
        result=result,
        ok=True,
        source="wal",
        sidecar=True,
    )


def _turn(log: tuple[JsonlSessionStore, str], user: str, reply: str, turn_id: str) -> None:
    store, session_id = log
    store.append_turn_detail(session_id, "chat", user, response=reply, turn_id=turn_id)


def test_tools_sit_between_the_request_and_the_reply_they_served(
    log: tuple[JsonlSessionStore, str],
) -> None:
    _tool(log, "shell_run", {"command": "kubectl get nodes"}, "node-a Ready")
    _tool(log, "skill_view", {"name": "repair-github-ci"}, "skill body " * 50)
    _turn(log, "check the nodes", "All nodes are ready.", "t1")
    _tool(log, "github_get_run", {"run_id": 18822}, '{"conclusion": "failure"}')
    _turn(log, "why did CI fail?", "Run 18822 failed on windows-latest.", "t2")

    digest = build_session_digest(log[1], demo=_NO_DEMO)

    assert digest.turns == 2
    text = digest.text
    assert (
        text.index("USER: check the nodes")
        < text.index("TOOL shell_run")
        < text.index("ASSISTANT: All nodes are ready.")
        < text.index("USER: why did CI fail?")
        < text.index('TOOL github_get_run {"run_id": 18822} → ok')
    )
    assert "skill_view" not in text


def test_tool_results_are_redacted_before_they_are_capped(
    log: tuple[JsonlSessionStore, str],
) -> None:
    fake_token = "ghp_" + ("a" * 36)
    _tool(log, "shell_run", {"command": "env"}, ("x" * 1_480) + f" GH_TOKEN={fake_token}")
    _turn(log, "show the env", "Done.", "t1")

    line = next(
        row
        for row in build_session_digest(log[1], demo=_NO_DEMO).text.splitlines()
        if row.startswith("TOOL")
    )

    result = line.split("→ ok: ", 1)[1]
    assert len(result) <= MAX_TOOL_RESULT_CHARS
    assert "ghp_" not in line


def test_the_newest_turns_survive_the_budget(log: tuple[JsonlSessionStore, str]) -> None:
    for index in range(40):
        _turn(log, f"question {index:02d} " + "q" * 200, f"answer {index:02d}", f"t{index}")

    digest = build_session_digest(log[1], demo=_NO_DEMO, max_chars=2_000)

    assert len(digest.text) <= 2_000
    assert "question 39" in digest.text
    assert "question 00" not in digest.text


def test_a_turn_with_too_many_tool_calls_keeps_its_request_reply_and_last_calls(
    log: tuple[JsonlSessionStore, str],
) -> None:
    for index in range(30):
        _tool(log, "shell_run", {"command": f"step {index:02d}"}, "y" * 900)
    _turn(log, "fix the flaky test", "Pinned the random seed; CI is green.", "t1")

    digest = build_session_digest(log[1], demo=_NO_DEMO, max_chars=5_000)

    assert len(digest.text) <= 5_000
    assert digest.text.startswith("USER: fix the flaky test")
    assert digest.text.endswith("ASSISTANT: Pinned the random seed; CI is green.")
    assert "earlier tool calls omitted" in digest.text
    assert "step 29" in digest.text and "step 00" not in digest.text


def test_demo_turns_are_dropped_by_turn_id_without_touching_lookalikes(
    log: tuple[JsonlSessionStore, str],
) -> None:
    _tool(log, "seed_ci_repair_demo", {"owner": "octocat"}, "created opensre-ci-repair-demo-ab12")
    _turn(log, "yes", "Demo repository ready.", "t-demo")
    _turn(log, "yes", "Restarted the payments deploy.", "t-real")

    digest = build_session_digest(
        log[1], demo=DemoTurns(turn_ids=frozenset({"t-demo"}), user_texts=frozenset({"yes"}))
    )

    assert digest.demo_turns_dropped == 1
    assert "opensre-ci-repair-demo" not in digest.text
    assert "Restarted the payments deploy." in digest.text


@pytest.mark.parametrize("latest_is_demo", [True, False])
def test_the_turn_still_running_follows_the_latest_turns_demo_flag(
    log: tuple[JsonlSessionStore, str], latest_is_demo: bool
) -> None:
    _turn(log, "check the nodes", "All nodes are ready.", "t1")
    _tool(log, "shell_run", {"command": "kubectl rollout status"}, "rollout complete")

    digest = build_session_digest(log[1], demo=DemoTurns(latest_is_demo=latest_is_demo))

    assert ("rollout complete" in digest.text) is not latest_is_demo


def test_a_log_of_demo_turns_only_is_empty_and_not_replaced_by_the_transcript(
    log: tuple[JsonlSessionStore, str],
) -> None:
    request = "Run one repair in OpenSRE Cloud"
    _turn(log, request, "Repaired PR #1.", "t-demo")

    digest = build_session_digest(
        log[1],
        demo=DemoTurns(
            turn_ids=frozenset({"t-demo"}),
            user_texts=frozenset({request}),
            latest_is_demo=True,
            latest_turn_id="t-demo",
            latest_user_text=request,
        ),
        transcript=[("user", request), ("assistant", "Repaired PR #1.")],
    )

    assert digest.empty


@pytest.mark.parametrize("logged", [False, True])
def test_the_digest_ends_with_the_turn_that_queued_the_pass(
    log: tuple[JsonlSessionStore, str], logged: bool
) -> None:
    """A pass is queued as its turn is recorded, before that turn's messages are logged.

    Whether or not they are logged by the time the pass reads the log, the
    digest holds that turn with its tools, and nothing from a later turn.
    """
    for index in range(5):
        _turn(log, f"question {index}", f"answer {index}", f"t{index}")
    _tool(log, "github_get_run", {"run_id": 18822}, '{"conclusion": "failure"}')
    request, reply = "why did run 18822 fail?", "It failed on windows-latest."
    if logged:
        _turn(log, request, reply, "t5")
        _tool(log, "seed_ci_repair_demo", {"owner": "octocat"}, "created a demo repository")

    digest = build_session_digest(
        log[1],
        demo=DemoTurns(latest_turn_id="t5", latest_user_text=request),
        transcript=[
            ("user", "question 4"),
            ("assistant", "answer 4"),
            ("user", request),
            ("assistant", reply),
        ],
    )

    assert digest.turns == 6
    assert digest.text.endswith(
        f"USER: {request}\n"
        'TOOL github_get_run {"run_id": 18822} → ok: {"conclusion": "failure"}\n'
        f"ASSISTANT: {reply}"
    )
    assert "seed_ci_repair_demo" not in digest.text


@pytest.mark.parametrize("source", ["compaction record", "session-summary message"])
def test_a_summary_of_earlier_turns_never_reaches_the_digest(
    log: tuple[JsonlSessionStore, str], source: str
) -> None:
    """A summary blends demo turns into prose the demo fence cannot attribute to them."""
    store, session_id = log
    summary = "Seeded octocat/opensre-ci-repair-demo-ab12 and repaired its PR #1."
    _turn(log, "yes", "Demo repository ready.", "t-demo")
    if source == "compaction record":
        store.append_compaction(
            session_id, summary=summary, first_kept_entry_id="", before_chars=1, after_chars=1
        )
    else:
        store.append_message(session_id, role="assistant", content=SESSION_SUMMARY_PREFIX + summary)
    _turn(log, "check the payments deploy", "The deploy is healthy.", "t-real")

    digest = build_session_digest(
        session_id,
        demo=DemoTurns(turn_ids=frozenset({"t-demo"}), user_texts=frozenset({"yes"})),
    )

    assert "opensre-ci-repair-demo" not in digest.text
    assert digest.text == "USER: check the payments deploy\nASSISTANT: The deploy is healthy."


def test_sessions_without_a_log_use_the_transcript_minus_demo_turns() -> None:
    digest = build_session_digest(
        "no-such-session",
        demo=DemoTurns(user_texts=frozenset({"Analyze & improve a repo (recommended)"})),
        transcript=[
            ("assistant", f"{SESSION_SUMMARY_PREFIX}Seeded opensre-ci-repair-demo-ab12."),
            ("user", "Analyze & improve a repo  (recommended)"),
            ("assistant", "main was red 30% of the month"),
            ("user", "our prod cluster is eks-prod-1"),
            ("assistant", "noted"),
        ],
    )

    assert digest.turns == 1
    assert "eks-prod-1" in digest.text
    assert "red 30%" not in digest.text
    assert "opensre-ci-repair-demo" not in digest.text


def test_turns_new_carried_in_stay_with_the_session_they_came_from(
    log: tuple[JsonlSessionStore, str],
) -> None:
    """``/new`` copies the conversation; its demo turns were fenced by the old session only."""
    store, session_id = log
    carried = [("user", "yes"), ("assistant", "Seeded octocat/opensre-ci-repair-demo-ab12.")]
    for role, content in carried:
        store.append_message(
            session_id,
            role=role,
            content=content,
            metadata={"kind": "chat", CARRIED_MESSAGE_METADATA_KEY: True},
        )

    # Closed right after /new: the transcript still ends with the carried exchange.
    right_after = build_session_digest(session_id, demo=_NO_DEMO, transcript=carried)
    _turn(log, "check the payments deploy", "The deploy is healthy.", "t-real")
    later = build_session_digest(
        session_id,
        demo=_NO_DEMO,
        transcript=[
            *carried,
            ("user", "check the payments deploy"),
            ("assistant", "The deploy is healthy."),
        ],
    )

    assert right_after.turns == 0
    assert "opensre-ci-repair-demo" not in right_after.text
    assert later.text == "USER: check the payments deploy\nASSISTANT: The deploy is healthy."
