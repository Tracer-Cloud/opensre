"""The action turn records the system prompt, skill body, and other model context."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from config.prompt_log import PromptLogConfig
from core.agent_harness.prompts.action.assemble import action_prompt_skill_and_context
from core.agent_harness.prompts.kernel.envelope import (
    PromptBlock,
    PromptBlockId,
    PromptBlockKind,
    PromptEnvelope,
    PromptTier,
)
from infrastructure.analytics.prompt_log.model_prompt import (
    record_action_model_prompt,
    record_model_blocks,
    skill_prompt_from_tool_results,
)
from infrastructure.analytics.prompt_log.recorder import PromptRecorder, current_recorder
from infrastructure.analytics.provider import JsonValue
from surfaces.interactive_shell.session import Session


def test_action_prompt_split_keeps_the_skill_out_of_the_other_context() -> None:
    envelope = PromptEnvelope.from_blocks(
        [
            PromptBlock(
                id=PromptBlockId.ACTION_SYSTEM_BASE,
                kind=PromptBlockKind.SYSTEM,
                tier=PromptTier.STABLE,
                content="SYSTEM",
            ),
            PromptBlock(
                id=PromptBlockId.ACTIVE_SKILL,
                kind=PromptBlockKind.RULE,
                tier=PromptTier.EPHEMERAL,
                content="ACTIVE SKILL: repair\nDo the repair.",
            ),
            PromptBlock(
                id=PromptBlockId.RECENT_CONVERSATION,
                kind=PromptBlockKind.CONVERSATION,
                tier=PromptTier.EPHEMERAL,
                content="RECENT CONVERSATION\nuser: earlier",
            ),
        ]
    )
    skill, context = action_prompt_skill_and_context(envelope)
    assert skill == "ACTIVE SKILL: repair\nDo the repair."
    assert "Do the repair." not in context
    assert "RECENT CONVERSATION" in context
    assert "SYSTEM" not in context


def test_skill_view_results_keep_the_body_the_model_read() -> None:
    call = SimpleNamespace(name="skill_view")
    execution = SimpleNamespace(
        details={"name": "repair-github-ci", "content": "Follow the repair steps."},
        content="ignored",
    )
    other = SimpleNamespace(name="shell_run")
    assert (
        skill_prompt_from_tool_results([(other, execution), (call, execution)])
        == "repair-github-ci\nFollow the repair steps."
    )


def test_recorder_sends_redacted_model_prompt_fields(monkeypatch, tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=False,
        posthog_enabled=True,
        redact=True,
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
    recorder = PromptRecorder.start(session=session, text="fix ci", turn_kind="agent")
    assert recorder is not None
    token = current_recorder.set(recorder)
    try:
        record_action_model_prompt(
            SimpleNamespace(
                final_system_prompt="Bearer token-value-12345678901234567890",
                tool_results=[
                    (
                        SimpleNamespace(name="skill_view"),
                        SimpleNamespace(
                            details={
                                "name": "repair-github-ci",
                                "content": "Follow the repair steps.",
                            },
                            content="",
                        ),
                    )
                ],
            ),
            skill="ACTIVE SKILL: repair",
            context="RECENT CONVERSATION",
        )
    finally:
        current_recorder.reset(token)
    recorder.set_response("done")
    recorder.flush()
    assert captured
    assert captured[0]["$ai_input"] == [{"role": "user", "content": "fix ci"}]
    assert "Bearer [REDACTED]" in str(captured[0]["model_system_prompt"])
    assert "token-value-" not in str(captured[0]["model_system_prompt"])
    assert captured[0]["model_skill_prompt"] == (
        "ACTIVE SKILL: repair\n\nrepair-github-ci\nFollow the repair steps."
    )
    assert captured[0]["model_context"] == "RECENT CONVERSATION"


def test_recorder_truncates_an_oversized_system_prompt(monkeypatch, tmp_path: Path) -> None:
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
    recorder = PromptRecorder.start(session=session, text="fix ci", turn_kind="agent")
    assert recorder is not None
    recorder.set_model_prompt(system="S" * 90_000, skill="", context="")
    recorder.set_response("done")
    recorder.flush()
    system = str(captured[0]["model_system_prompt"])
    assert system.endswith("[truncated]")
    assert len(system) < 90_000


def test_recorder_writes_model_blocks_to_the_local_log_and_the_event(
    monkeypatch, tmp_path: Path
) -> None:
    captured: list[dict[str, object]] = []
    log_path = tmp_path / "prompt_log.jsonl"
    cfg = PromptLogConfig(
        enabled=True,
        local_enabled=True,
        posthog_enabled=True,
        redact=True,
        max_chars=1000,
        log_path=log_path,
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.PromptLogConfig.load", lambda: cfg
    )
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.recorder.capture_ai_generation",
        lambda payload: captured.append(payload),
    )
    blocks: dict[str, JsonValue] = {
        "blocks": {"action-agent-system-base": {"tier": "stable", "chars": 400, "tokens": 100}},
        "history": {"messages": 4, "chars": 80, "tokens": 20, "tool_turns": 1},
        "total": {"chars": 480, "tokens": 120},
        "tool_schema_count": 12,
    }
    recorder = PromptRecorder.start(session=Session(), text="fix ci", turn_kind="agent")
    assert recorder is not None
    token = current_recorder.set(recorder)
    try:
        record_model_blocks(blocks)
    finally:
        current_recorder.reset(token)
    recorder.set_response("done")
    recorder.flush()

    local = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
    assert local["model_blocks"] == blocks
    assert captured[0]["model_blocks"] == blocks
