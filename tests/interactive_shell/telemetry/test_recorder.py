from __future__ import annotations

from pathlib import Path

from config.prompt_log import PromptLogConfig
from core.agent_harness.accounting.token_accounting import LlmRunInfo
from infrastructure.analytics.prompt_log.recorder import PromptRecorder
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry import integration_snapshot


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
        "surfaces.interactive_shell.telemetry.integration_snapshot.build_turn_integration_snapshot",
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
        "surfaces.interactive_shell.telemetry.integration_snapshot.build_turn_integration_snapshot",
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
        "surfaces.interactive_shell.telemetry.integration_snapshot.get_registered_tools",
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
        "surfaces.interactive_shell.telemetry.integration_snapshot.build_turn_integration_snapshot",
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
        "surfaces.interactive_shell.telemetry.integration_snapshot.build_turn_integration_snapshot",
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
        "surfaces.interactive_shell.telemetry.integration_snapshot.build_turn_integration_snapshot",
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
        "surfaces.interactive_shell.telemetry.integration_snapshot.build_turn_integration_snapshot",
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
        "surfaces.interactive_shell.telemetry.integration_snapshot.build_turn_integration_snapshot",
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
        "surfaces.interactive_shell.telemetry.integration_snapshot.build_turn_integration_snapshot",
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
