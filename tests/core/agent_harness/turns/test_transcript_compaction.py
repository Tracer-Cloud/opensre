"""Session compaction: the text fallback and token-based structured compaction."""

from __future__ import annotations

from typing import Any

import pytest

from config.constants.conversation_history import (
    OPENSRE_HISTORY_TOKEN_BUDGET_ENV,
    OPENSRE_LLM_COMPACTION_ENV,
    OPENSRE_STRUCTURED_HISTORY_ENV,
)
from core.agent_harness.turns.structured_history import build_turn_evidence
from core.agent_harness.turns.transcript_compaction import (
    compact_session_branch,
    should_compact,
)
from core.state import MutableAgentState
from core.state.transcript_window import SESSION_SUMMARY_PREFIX


class _FakeStorage:
    def __init__(self) -> None:
        self.compactions: list[dict[str, Any]] = []

    def append_compaction(self, session_id: str, **kwargs: Any) -> str:
        self.compactions.append({"session_id": session_id, **kwargs})
        return "entry-id"


class _FakeSession:
    def __init__(self, messages: list[tuple[str, str]]) -> None:
        self.agent = MutableAgentState(messages=messages)
        self.store = _FakeStorage()
        self.session_id = "session-under-test"


def _long_turns(n: int) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for i in range(n):
        out.append(("user", f"question {i} " + "y" * 200))
        out.append(("assistant", f"answer {i} " + "z" * 200))
    return out


@pytest.fixture
def text_history(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_STRUCTURED_HISTORY_ENV, "0")


@pytest.mark.usefixtures("text_history")
def test_char_compaction_preserves_existing_window_summary() -> None:
    """A fact sitting past the 700-char excerpt cut inside a prior window
    summary must survive char-threshold compaction."""
    deep_fact = "deep-fact-prod-eu-42"
    prior_summary = "x" * 750 + " " + deep_fact
    messages = [
        ("assistant", f"{SESSION_SUMMARY_PREFIX}{prior_summary}"),
        *_long_turns(10),
    ]
    session = _FakeSession(messages)

    result = compact_session_branch(session)

    assert result is not None
    head_role, head_content = session.agent.messages[0]
    assert head_role == "assistant"
    assert head_content.startswith(SESSION_SUMMARY_PREFIX)
    assert deep_fact in head_content
    summaries = [m for m in session.agent.messages if m[1].startswith(SESSION_SUMMARY_PREFIX)]
    assert len(summaries) == 1


@pytest.mark.usefixtures("text_history")
def test_char_compaction_without_prior_summary_unchanged() -> None:
    session = _FakeSession(_long_turns(10))

    result = compact_session_branch(session)

    assert result is not None
    head = session.agent.messages[0][1]
    assert head.startswith(SESSION_SUMMARY_PREFIX)
    assert "Compacted" in head
    assert len(session.agent.messages) == 9


def _session_with_evidence(turns: int, *, result_chars: int) -> _FakeSession:
    session = _FakeSession([])
    for index in range(turns):
        tool_items = (
            {
                "kind": "assistant",
                "text": "",
                "tool_calls": [{"id": f"call_{index}", "name": "github_cli", "input": {}}],
            },
            {
                "kind": "tool_results",
                "results": [
                    {
                        "id": f"call_{index}",
                        "name": "github_cli",
                        "content": f"run-{index} failed " + "x" * result_chars,
                    }
                ],
            },
        )
        user_text, reply = f"check run {index}", f"Run {index} failed."
        session.agent.record_turn(
            user_text, reply, evidence=build_turn_evidence(user_text, reply, tool_items)
        )
    return session


def test_summarizer_receives_the_recorded_observation_without_an_extra_cut() -> None:
    session = _session_with_evidence(12, result_chars=8_000)
    result = session.agent.turn_evidence[0].items[2]["results"][0]
    result["content"] = "START\n" + "x" * 4_000 + "\nrun-id-9312\n" + "x" * 4_000 + "\nEND"
    prompts: list[str] = []

    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return "Retain run-id-9312."

    compacted = compact_session_branch(session, summarizer=summarize)

    assert compacted is not None
    assert result["content"] in prompts[0]


def test_structured_compaction_summarizes_old_turns_and_keeps_recent_evidence() -> None:
    session = _session_with_evidence(12, result_chars=8_000)
    prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        prompts.append(prompt)
        return "Runs 0-8 failed on github_cli; the user is triaging CI."

    result = compact_session_branch(session, summarizer=summarizer)

    assert result is not None
    # The summarizer saw what the tools returned, not only the replies.
    assert "run-0 failed" in prompts[0]
    head_role, head = session.agent.messages[0]
    assert head_role == "assistant"
    assert (
        head == f"{SESSION_SUMMARY_PREFIX}Runs 0-8 failed on github_cli; the user is triaging CI."
    )
    kept_user = [text for role, text in session.agent.messages[1:] if role == "user"]
    assert kept_user[-1] == "check run 11"
    assert [record.user_text for record in session.agent.turn_evidence] == kept_user
    # The record keeps what stayed, so a resumed session restarts from the same state.
    [record] = session.store.compactions
    assert record["replacement_messages"][-1] == ["assistant", "Run 11 failed."]
    assert len(record["replacement_evidence"]) == len(kept_user)


def test_structured_compaction_falls_back_when_the_model_fails() -> None:
    session = _session_with_evidence(12, result_chars=8_000)

    def failing(_prompt: str) -> str:
        raise RuntimeError("provider unavailable")

    result = compact_session_branch(session, summarizer=failing)

    assert result is not None
    assert "Compacted" in session.agent.messages[0][1]


def test_manual_compaction_keeps_only_the_newest_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_LLM_COMPACTION_ENV, "0")
    session = _session_with_evidence(3, result_chars=10)

    result = compact_session_branch(session, manual=True)

    assert result is not None
    assert session.agent.messages[1:] == [("user", "check run 2"), ("assistant", "Run 2 failed.")]


def test_auto_compaction_waits_for_the_token_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_HISTORY_TOKEN_BUDGET_ENV, "10000")
    small = _session_with_evidence(3, result_chars=100)
    large = _session_with_evidence(8, result_chars=8_000)

    assert not should_compact(small)
    assert should_compact(large)
    assert compact_session_branch(small) is None


def test_many_short_turns_are_summarized_with_their_tool_output() -> None:
    session = _session_with_evidence(61, result_chars=10)
    prompts: list[str] = []

    def summarizer(prompt: str) -> str:
        prompts.append(prompt)
        return "Runs 0-40 were checked."

    # Well under the token budget, but past the turn trigger: the summary must
    # come from a model that saw the old turns' tool output, before the
    # text-only backstop would cut them.
    assert should_compact(session)
    assert compact_session_branch(session, summarizer=summarizer) is not None
    assert "run-0 failed" in prompts[0]
    kept_turns = [text for role, text in session.agent.messages if role == "user"]
    assert len(kept_turns) <= 20
    assert kept_turns[-1] == "check run 60"
