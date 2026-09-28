"""Regressions for measured, missing, and inherited analytics evidence."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from config.prompt_log import PromptLogConfig
from infrastructure.analytics import capture
from infrastructure.analytics.event_properties import build_cli_invoked_properties
from infrastructure.analytics.prompt_log import recorder as prompt_module
from infrastructure.analytics.usage_context import bound_usage_context, merge_usage_enrichment
from surfaces.interactive_shell.telemetry import integration_snapshot


def test_cli_option_is_separate_from_terminal_measurement(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))
    monkeypatch.setattr("sys.stdout", SimpleNamespace(isatty=lambda: False))
    props = build_cli_invoked_properties(
        entrypoint="opensre", command_parts=["config", "show"], interactive=True
    )
    assert "interactive" not in props
    assert props["interactive_option"] is True
    assert props["stdin_is_tty"] is False
    assert props["stdout_is_tty"] is False


def test_turn_context_overrides_process_fallback_but_not_event() -> None:
    with bound_usage_context(surface="slack", session_id="slack-session"):
        props = merge_usage_enrichment({}, defaults={"surface": "cli"})
        explicit = merge_usage_enrichment({"surface": "telegram"}, defaults={"surface": "cli"})
    assert props["surface"] == "slack"
    assert props["session_id"] == "slack-session"
    assert explicit["surface"] == "telegram"


def test_no_actions_have_no_success_rate(monkeypatch) -> None:
    emitted = []
    monkeypatch.setattr(capture, "_capture", lambda _event, props: emitted.append(props))
    capture.capture_terminal_actions_executed(
        planned_count=0, executed_count=0, executed_success_count=0
    )
    assert "success_rate_bucket" not in emitted[0]


def test_failed_inventory_does_not_claim_zero(monkeypatch) -> None:
    def unavailable(*args, **kwargs):
        raise RuntimeError("unavailable")

    monkeypatch.setattr("integrations.verify.resolve_effective_integrations", unavailable)
    snapshot = integration_snapshot.build_turn_integration_snapshot(None)
    assert snapshot["integration_snapshot_status"] == "unavailable"
    assert "configured_integrations" not in snapshot
    assert "connected_integrations_count" not in snapshot


def test_prompt_measurements_and_outcomes_preserve_missingness(monkeypatch, tmp_path: Path) -> None:
    emitted = []
    monkeypatch.setattr(prompt_module, "capture_ai_generation", emitted.append)
    config = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=False,
        max_chars=1000,
        log_path=tmp_path / "unused.jsonl",
    )
    session = SimpleNamespace(history=[{"type": "slash", "slash_outcome": "unknown_command"}])
    recorder = prompt_module.PromptRecorder(
        config=config,
        session=session,
        session_id="session",
        turn_id="turn",
        turn_kind="agent",
        prompt="hello",
    )
    recorder.set_response(
        "answer",
        SimpleNamespace(
            model="model",
            provider="provider",
            latency_ms=1,
            input_tokens=None,
            output_tokens=0,
        ),
    )
    recorder.flush()
    props = emitted[0]
    assert "$ai_input_tokens" not in props
    assert props["$ai_output_tokens"] == 0
    assert props["token_usage_status"] == "partial"
    assert props["turn_outcome"] == "completed"
    assert props["llm_attempted"] is True
    assert props["response_source"] == "captured"
    assert "slash_outcome" not in props

    empty = prompt_module.PromptRecorder(
        config=config,
        session=session,
        session_id="session",
        turn_id="empty",
        turn_kind="agent",
        prompt="hello",
    )
    empty.flush()
    assert emitted[1]["turn_outcome"] == "unknown"
    assert emitted[1]["response_source"] == "synthetic"
    assert "llm_attempted" not in emitted[1]
