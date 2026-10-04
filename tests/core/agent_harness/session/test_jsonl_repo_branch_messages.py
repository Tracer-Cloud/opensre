"""Restore-path message assembly from a persisted session branch."""

from __future__ import annotations


def test_messages_for_branch_uses_session_summary_prefix() -> None:
    from core.agent_harness.session.persistence.jsonl_repo import _messages_for_branch
    from core.state.transcript_window import SESSION_SUMMARY_PREFIX

    branch = [
        {"type": "compaction", "summary": "All checks passed."},
    ]
    messages = _messages_for_branch(branch)
    assert len(messages) == 1
    assert messages[0] == ("assistant", f"{SESSION_SUMMARY_PREFIX}All checks passed.")


def test_a_compaction_that_kept_its_turns_restarts_the_transcript() -> None:
    from core.agent_harness.session.persistence.jsonl_repo import (
        _messages_for_branch,
        _turn_evidence_for_branch,
    )
    from core.state.transcript_window import SESSION_SUMMARY_PREFIX

    kept = {"user_text": "check run 11", "assistant_text": "Run 11 failed.", "items": []}
    branch = [
        {"type": "message", "role": "user", "content": "check run 0"},
        {"type": "message", "role": "assistant", "content": "Run 0 failed."},
        {"type": "custom_message", "custom_type": "turn_evidence", "content": {"old": True}},
        {
            "type": "compaction",
            "summary": "Runs 0-10 failed.",
            "replacement_messages": [["user", "check run 11"], ["assistant", "Run 11 failed."]],
            "replacement_evidence": [kept],
        },
        {"type": "message", "role": "user", "content": "rerun it"},
        {"type": "message", "role": "assistant", "content": "Rerunning."},
    ]

    assert _messages_for_branch(branch) == [
        ("assistant", f"{SESSION_SUMMARY_PREFIX}Runs 0-10 failed."),
        ("user", "check run 11"),
        ("assistant", "Run 11 failed."),
        ("user", "rerun it"),
        ("assistant", "Rerunning."),
    ]
    assert _turn_evidence_for_branch(branch) == [kept]


def test_turn_evidence_survives_a_session_file_round_trip(tmp_path, monkeypatch) -> None:
    from core.agent_harness.session.lifecycle import SessionManager
    from core.agent_harness.session.persistence import paths as storage_paths
    from core.agent_harness.session.persistence.jsonl_repo import JsonlSessionRepo
    from core.agent_harness.session.persistence.jsonl_store import JsonlSessionStore
    from core.agent_harness.session.session_core import SessionCore
    from core.agent_harness.turns.conversation_recording import record_conversation_turn

    monkeypatch.setattr(storage_paths, "sessions_dir", lambda: tmp_path)
    store = JsonlSessionStore()
    session = SessionCore(store=store)
    store.open_session(session)
    items = (
        {
            "kind": "assistant",
            "text": "",
            "tool_calls": [{"id": "call_1", "name": "ci_runs", "input": {}}],
        },
        {
            "kind": "tool_results",
            "results": [{"id": "call_1", "name": "ci_runs", "content": "9312"}],
        },
    )
    record_conversation_turn(session, "Is CI failing?", "Run 9312 is failing.", tool_items=items)
    store.append_turn_detail(
        session.session_id, "chat", "Is CI failing?", response="Run 9312 is failing."
    )

    restored = SessionCore(store=store)
    SessionManager().restore_context(restored, JsonlSessionRepo().load_session(session.session_id))

    assert restored.cli_agent_messages == [
        ("user", "Is CI failing?"),
        ("assistant", "Run 9312 is failing."),
    ]
    [evidence] = restored.turn_evidence
    assert evidence.has_tool_activity
    assert evidence.to_json() == session.turn_evidence[0].to_json()


def test_new_records_the_carried_conversation_in_the_new_session_file() -> None:
    from core.agent_harness.session import InMemorySessionStore, SessionCore, SessionManager
    from core.agent_harness.turns.structured_history import build_turn_evidence

    store = InMemorySessionStore()
    session = SessionCore(store=store)
    messages = [("user", "Is CI failing?"), ("assistant", "Run 9312 is failing.")]
    evidence = [build_turn_evidence("Is CI failing?", "Run 9312 is failing.", ())]
    manager = SessionManager(store=store)
    manager.rotate_in_place(session)

    manager.carry_forward(session, messages=messages, evidence=evidence)

    records = store.read(session.session_id)
    assert [(r["role"], r["content"]) for r in records if r["type"] == "message"] == messages
    carried = [r for r in records if r.get("custom_type") == "turn_evidence"]
    assert [r["content"] for r in carried] == [evidence[0].to_json()]
