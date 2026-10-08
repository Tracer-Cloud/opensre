from __future__ import annotations

from pathlib import Path

from config.prompt_log import PromptLogConfig
from core.agent_harness.accounting.token_accounting import LlmRunInfo
from core.agent_harness.session.pending_choice import AskUserQuestion, PendingUserChoice
from infrastructure.analytics.prompt_log.lifecycle import record_prompt_turn, recorded_prompt_text
from infrastructure.analytics.prompt_log.recorder import PromptRecorder
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from surfaces.shared import integration_telemetry as integration_snapshot
from tests.core.agent.orchestration.action_execution_test_harness import (
    ActionExecutionHarness,
    FakeActionLLM,
    no_tool_response,
    tool_response,
)


def test_prompt_recorder_start_respects_supported_turns(monkeypatch, tmp_path: Path) -> None:
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=False,
        redact=False,
        max_chars=100,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = Session()
    assert PromptRecorder.start(session=session, text="hello", turn_kind="slash") is None
    assert PromptRecorder.start(session=session, text="hello", turn_kind="agent") is not None


def test_prompt_recorder_for_background_task_uses_task_id_as_trace(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.for_background_task(
        session=session, command="opensre integrations verify grafana", task_id="ab247135"
    )
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response("command failed (exit 1)\nboom")
    recorder.flush()
    assert captured
    assert captured[0]["cli_turn_kind"] == "background_task"
    assert captured[0]["$ai_trace_id"] == "ab247135"
    assert captured[0]["$ai_input"][0]["content"] == "opensre integrations verify grafana"
    assert captured[0]["$ai_output_choices"][0]["content"] == "command failed (exit 1)\nboom"


def test_prompt_recorder_for_background_task_disabled_returns_none(monkeypatch) -> None:
    cfg = PromptLogConfig(enabled=False)
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = Session()
    assert PromptRecorder.for_background_task(session=session, command="x", task_id="t") is None


def test_prompt_recorder_flush_writes_and_redacts(monkeypatch, tmp_path: Path) -> None:
    log_path = tmp_path / "prompt_log.jsonl"
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=True,
        posthog_enabled=False,
        redact=True,
        max_chars=1000,
        log_path=log_path,
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    session = Session()
    recorder = PromptRecorder.start(
        session=session,
        text="Bearer token-value-12345678901234567890",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response(
        "sk-ant-abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        LlmRunInfo(model="m", provider="p", latency_ms=10),
    )
    recorder.flush()
    payload = log_path.read_text(encoding="utf-8")
    assert "Bearer [REDACTED]" in payload
    assert "[REDACTED:anthropic_key]" in payload


def test_prompt_recorder_sends_ai_generation(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {
            "connected_integrations": [],
            "connected_integrations_count": 0,
            "configured_integrations": [],
            "integration_snapshot_source": "runtime_config",
        },
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response("world", LlmRunInfo(model="gpt-test", provider="openai", latency_ms=50))
    recorder.flush()
    assert captured
    assert captured[0]["$ai_model"] == "gpt-test"
    assert "$ai_input_tokens" not in captured[0]
    assert captured[0]["token_usage_status"] == "unavailable"
    assert captured[0]["connected_integrations"] == []
    assert captured[0]["connected_integrations_count"] == 0
    assert captured[0]["configured_integrations"] == []
    assert captured[0]["integration_snapshot_source"] == "runtime_config"


def test_prompt_recorder_sends_connected_integrations(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {
            "connected_integrations": ["github"],
            "connected_integrations_count": 1,
            "configured_integrations": ["github"],
            "integration_snapshot_source": "runtime_config",
        },
    )
    session = Session()
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response("world", LlmRunInfo(model="gpt-test", provider="openai", latency_ms=50))
    recorder.flush()
    assert captured[0]["connected_integrations"] == ["github"]
    assert captured[0]["connected_integrations_count"] == 1


def test_prompt_recorder_still_captures_when_tool_resolution_fails(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )

    def _boom() -> list[object]:
        raise RuntimeError("tool registry blew up")

    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.get_registered_tools",
        _boom,
    )

    session = Session()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog",)
    session.resolved_integrations_cache = {"datadog": {"api_key": "x", "app_key": "y"}}
    recorder = PromptRecorder.start(
        session=session,
        text="hello",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response("world", LlmRunInfo(model="gpt-test", provider="openai", latency_ms=50))
    recorder.flush()
    assert captured
    assert captured[0]["$ai_model"] == "gpt-test"
    assert captured[0]["configured_integrations"] == ["datadog"]
    assert "connected_integrations" not in captured[0]
    assert captured[0]["integration_snapshot_status"] == "partial"


def test_prompt_recorder_uses_no_conversational_agent_for_explicit_static_dispatch(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.start(
        session=session,
        text="/help",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response("slash /help (succeeded)")
    recorder.set_llm_attempted(False)
    recorder.flush()
    assert captured[0]["$ai_model"] == "no_conversational_agent"
    assert captured[0]["$ai_provider"] == "no_conversational_agent"


def test_prompt_recorder_uses_prompt_fallback_when_response_empty(
    monkeypatch, tmp_path: Path
) -> None:
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {},
    )
    captured: list[dict[str, object]] = []
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    session.record("slash", "/help", ok=True, response_text="slash /help (succeeded)")
    recorder = PromptRecorder.start(session=session, text="/help", turn_kind="agent")
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response("   ")
    recorder.flush()
    assert captured[0]["$ai_output_choices"][0]["content"] == "terminal turn handled: /help"


def test_prompt_fallback_never_reaches_the_session_conversation(
    monkeypatch, tmp_path: Path
) -> None:
    """Analytics wants a response on every row; the conversation must not take it.

    The fallback was written to the session file as the assistant's reply, so
    ``/resume`` replayed "terminal turn handled: /resume" as model prose and fed
    it back to the model as its own prior turn.
    """
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=False,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {},
    )
    written: list[str | None] = []

    def _append_turn_detail(_session_id, _kind, _prompt, *, response=None, **_kwargs):
        written.append(response)

    session = Session()
    monkeypatch.setattr(session.store, "append_turn_detail", _append_turn_detail)
    recorder = PromptRecorder.start(session=session, text="/resume", turn_kind="agent")
    assert recorder is not None
    recorder.set_response("   ")
    recorder.flush()

    assert written == []


def test_a_reply_less_turn_leaves_no_orphan_user_message(monkeypatch, tmp_path: Path) -> None:
    """`append_turn_detail` writes the prompt unconditionally and the reply only
    if present, so persisting a reply-less turn left an unpaired user message —
    adjacent user roles in `cli_agent_messages` on the next resume, and a
    bookkeeping line such as `/resume` handed back to the model as context.
    """
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=False,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {},
    )
    session = Session()

    recorder = PromptRecorder.start(session=session, text="/resume", turn_kind="agent")
    assert recorder is not None
    recorder.flush()

    assert session.agent.messages == []

    answered = PromptRecorder.start(session=session, text="why is redis slow?", turn_kind="agent")
    assert answered is not None
    answered.set_response("Pool exhaustion.")
    answered.flush()

    roles = [role for role, _ in session.agent.messages]
    assert roles == [] or roles == ["user", "assistant"]


def test_prompt_recorder_set_error_adds_structured_properties(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.start(session=session, text="/investigate generic", turn_kind="agent")
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_error("config", "ANTHROPIC_API_KEY not set")
    recorder.set_response(
        "slash /investigate generic (failed)\ninvestigation_failed (generic):\n"
        "ANTHROPIC_API_KEY not set"
    )
    recorder.flush()
    assert captured[0]["$ai_is_error"] is True
    assert captured[0]["$ai_error"] == "ANTHROPIC_API_KEY not set"
    assert captured[0]["error_kind"] == "config"
    # A generic config error alone says nothing about whether an LLM was used.
    assert "ai_error_kind" not in captured[0]
    assert captured[0]["$ai_model"] == "unknown"


def test_prompt_recorder_omits_error_properties_by_default(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.start(session=session, text="hello", turn_kind="agent")
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response("world")
    recorder.flush()
    assert captured[0]["$ai_is_error"] is False
    assert "$ai_error" not in captured[0]
    assert "error_kind" not in captured[0]


def _posthog_recorder(
    monkeypatch,
    tmp_path: Path,
    *,
    text: str,
    captured: list[dict[str, object]],
) -> PromptRecorder:
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    recorder = PromptRecorder.start(session=session, text=text, turn_kind="agent")
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    return recorder


def test_prompt_recorder_llm_provider_failure_never_uses_terminal_sentinel(
    monkeypatch, tmp_path: Path
) -> None:
    """Conversational prompt + provider failure must not be tagged no_conversational_agent."""
    captured: list[dict[str, object]] = []
    recorder = _posthog_recorder(monkeypatch, tmp_path, text="hi", captured=captured)
    error = (
        "Bedrock model 'us.anthropic.claude-sonnet-4-6' is not available for your account. "
        "Check Bedrock model access in the configured AWS region."
    )
    recorder.set_error("action_agent_error", error)
    recorder.set_response(error)
    recorder.flush()
    assert captured[0]["$ai_model"] == "unknown"
    assert captured[0]["$ai_provider"] == "unknown"
    assert captured[0]["ai_error_kind"] == "not_configured"
    assert "not available for your account" in captured[0]["$ai_output_choices"][0]["content"]


def test_prompt_recorder_llm_provider_failure_reports_attempted_model(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict[str, object]] = []
    recorder = _posthog_recorder(monkeypatch, tmp_path, text="hi", captured=captured)
    recorder.set_error("assistant_error", "Anthropic authentication failed.")
    recorder.set_response(
        "",
        LlmRunInfo(model="claude-sonnet-4-6", provider="anthropic"),
    )
    recorder.flush()
    assert captured[0]["$ai_model"] == "claude-sonnet-4-6"
    assert captured[0]["$ai_provider"] == "anthropic"
    assert captured[0]["ai_error_kind"] == "auth"
    # Empty assistant text falls back to the error message, not the terminal fallback.
    assert captured[0]["$ai_output_choices"][0]["content"] == "Anthropic authentication failed."


def test_prompt_recorder_flush_resolves_error_message_after_empty_set_response(
    monkeypatch, tmp_path: Path
) -> None:
    """Flush-time fallback tolerates set_response before set_error."""
    captured: list[dict[str, object]] = []
    recorder = _posthog_recorder(monkeypatch, tmp_path, text="hi", captured=captured)
    recorder.set_response("")
    recorder.set_error("assistant_error", "provider failed")
    recorder.flush()
    assert captured[0]["$ai_output_choices"][0]["content"] == "provider failed"


def test_prompt_recorder_terminal_error_kinds_keep_terminal_sentinel(
    monkeypatch, tmp_path: Path
) -> None:
    """Background-task style errors (e.g. subprocess timeout) stay terminal-action turns."""
    captured: list[dict[str, object]] = []
    recorder = _posthog_recorder(monkeypatch, tmp_path, text="hi", captured=captured)
    recorder.set_error("timeout", "command timed out after 60 seconds")
    recorder.set_llm_attempted(False)
    recorder.set_response("command timed out after 60 seconds")
    recorder.flush()
    assert captured[0]["$ai_model"] == "no_conversational_agent"
    assert captured[0]["$ai_provider"] == "no_conversational_agent"
    assert "ai_error_kind" not in captured[0]


def test_prompt_recorder_uses_only_latest_slash_outcome(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "surfaces.shared.integration_telemetry.build_turn_integration_snapshot",
        lambda _session: {},
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    session.record(
        "slash",
        "/modle",
        ok=False,
        response_text="Unknown command: /modle.",
        slash_outcome="unknown_command",
    )
    session.record("slash", "/help", ok=True, response_text="slash /help (succeeded)")
    recorder = PromptRecorder.start(
        session=session,
        text="what integrations are configured?",
        turn_kind="agent",
    )
    assert recorder is not None
    recorder.set_properties(integration_snapshot.build_turn_integration_snapshot(session))
    recorder.set_response("github and datadog")
    recorder.flush()
    assert "slash_outcome" not in captured[0]


def test_choose_turn_is_named_by_the_queued_question() -> None:
    session = Session()
    session.pending_user_choice = PendingUserChoice(
        title="Which demo would you like me to run?",
        options=("Explore a repo", "Skip the demo"),
    )
    assert recorded_prompt_text("/choose", session) == "Which demo would you like me to run?"
    assert recorded_prompt_text("  /choose  ", session) == "Which demo would you like me to run?"
    batched = Session()
    batched.pending_user_choice = PendingUserChoice(
        title="Ask User",
        options=(),
        questions=(
            AskUserQuestion(label="Demo", title="Which demo?", options=("One", "Two")),
            AskUserQuestion(label="Repo", title="Which repository?", options=("acme/one",)),
        ),
    )
    assert recorded_prompt_text("/choose now", batched) == "Which demo?\nWhich repository?"


def test_choose_without_a_queued_question_stays_the_command() -> None:
    session = Session()
    assert recorded_prompt_text("/choose", session) == "/choose"
    assert recorded_prompt_text("/help", session) == "/help"
    session.pending_user_choice = PendingUserChoice(title="   ", options=("Yes", "No"))
    assert recorded_prompt_text("/choose", session) == "/choose"


def test_choose_turn_sends_the_question_as_the_prompt(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    session = Session()
    session.pending_user_choice = PendingUserChoice(
        title="Which demo would you like me to run?",
        options=("Explore a repo", "Skip the demo"),
    )
    with record_prompt_turn("/choose", session, surface="interactive_shell") as recorder:
        assert recorder is not None
        recorder.set_response("slash /choose (succeeded)")
    assert captured[0]["$ai_input"] == [
        {"role": "user", "content": "Which demo would you like me to run?"}
    ]


def test_a_turn_that_stopped_short_records_why_the_loop_stopped(
    monkeypatch, tmp_path: Path
) -> None:
    # Arrange: a work call fails, then the model tries to conclude three times.
    # The goal review refuses each attempt until the loop stops on its own limit.
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=True,
        max_chars=4000,
        log_path=tmp_path / "prompt_log.jsonl",
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    harness = ActionExecutionHarness(
        llm=FakeActionLLM(
            [
                tool_response("skill_view", {"name": "missing-skill", "reference": "metrics"}),
                no_tool_response("Done."),
                no_tool_response("Done."),
                no_tool_response("Done."),
                no_tool_response("I could not load the metrics reference."),
            ]
        )
    )
    session = Session()

    # Act
    with record_prompt_turn("show the CI metrics", session, surface="interactive_shell"):
        result = run_action_tool_turn(
            "show the CI metrics", session, harness.console, llm_factory=harness.llm_factory
        )

    # Assert: the turn result and $ai_generation both name the real stop.
    assert result.hit_iteration_cap is True
    assert result.stop_reason == "goal_unverified"
    generation = captured[0]
    assert generation["stop_reason"] == "goal_unverified"
    assert generation["goal_review_reason"] == "work_tool_failed"
    assert generation["last_failed_tool"] == "skill_view"
    assert generation["last_tool_error"] == "unknown reference 'metrics' for skill 'missing-skill'"
    assert (generation["tool_error_count"], generation["blocked_tool_calls"]) == (1, 0)
    assert generation["error_kind"] == "iteration_limit"
    assert generation["$ai_error"] == (
        "Agent stopped before producing a final answer. stop_reason=goal_unverified; "
        "goal_review_reason=work_tool_failed; last_failed_tool=skill_view; "
        "last_tool_error=unknown reference 'metrics' for skill 'missing-skill'"
    )
