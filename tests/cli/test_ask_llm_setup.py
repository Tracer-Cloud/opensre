"""`opensre ask` names the setup command that fixes a missing LLM, in CLI form.

A signed-out run without a usable provider is refused before any session or
agent work; a turn that still fails on a missing key names the catalog provider.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from config.account import AccountLLMRoute
from config.prompt_log import PromptLogConfig
from core.agent.run_io import AgentRunResult
from core.agent_harness.harness import AgentSession, SessionStartupResult
from core.agent_harness.session import SessionCore
from core.agent_harness.session.persistence.memory import InMemorySessionStore
from core.agent_harness.turns import action_driver
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult
from core.llm.readiness import LLMReadiness
from core.llm.transports.sdk.agent_clients import OpenAIAgentClient
from infrastructure.analytics.events import Event
from surfaces.cli.ask import service
from surfaces.cli.ask.service import AskExitCode, AskStatus

_PROMPT = "why is checkout slow?"
_ACCOUNT_ROUTE = AccountLLMRoute(base_url="https://app.opensre.test/api/llm", model="gpt-5.4-mini")


class _Analytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, dict[str, Any]]] = []

    def capture(self, event: Event, properties: dict[str, Any] | None = None) -> None:
        self.events.append((event, properties or {}))


class _KeyVanishedAgent:
    """Fails its first LLM call with the OpenAI SDK's missing-key error."""

    def __init__(self) -> None:
        self._react_iterations_used = 0
        self._react_executed: list[Any] = []
        self._react_hit_iteration_cap = False

    def run(self, _messages: Any) -> AgentRunResult:
        raise RuntimeError(
            "Missing credentials. Please pass an `api_key`, `workload_identity`, "
            "`admin_api_key`, or set the `OPENAI_API_KEY` or `OPENAI_ADMIN_KEY` "
            "environment variable."
        )


def _signed_out() -> None:
    return None


def _signed_in() -> AccountLLMRoute:
    return _ACCOUNT_ROUTE


def _turn_must_not_start(*_args: object, **_kwargs: object) -> TurnResult:
    raise AssertionError("an unconfigured LLM must be refused before any session or turn")


def _answered(*_args: object, **_kwargs: object) -> TurnResult:
    return TurnResult(
        final_intent="answer",
        action_result=ToolCallingTurnResult(
            planned_count=0,
            executed_count=0,
            executed_success_count=0,
            has_unhandled_clause=False,
            handled=False,
        ),
        assistant_response_text="answer",
    )


def _static_key(_env_var: str) -> str:
    return "sk-test"


@pytest.fixture
def custom_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signed-out machine pointed at a custom OpenAI-compatible gateway, with no key."""
    monkeypatch.setenv("GRAFANA_CONFIG_SKIP_ENV_FILE", "1")
    monkeypatch.setenv("LLM_PROVIDER", "custom-openai")
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://127.0.0.1:4000/v1")
    monkeypatch.setenv("CUSTOM_OPENAI_MODEL", "gateway-model")
    monkeypatch.delenv("CUSTOM_OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("config.account.account_llm_route", _signed_out)


@pytest.mark.parametrize(
    ("unset", "guidance"),
    [
        (
            "CUSTOM_OPENAI_API_KEY",
            "No API key is set for custom-openai. Run `opensre auth login custom-openai`",
        ),
        # The settings validator fires first and must not read as a missing key.
        (
            "CUSTOM_OPENAI_BASE_URL",
            "CUSTOM_OPENAI_BASE_URL is not set for custom-openai. Run `opensre onboard`",
        ),
    ],
)
@pytest.mark.usefixtures("custom_openai")
def test_unconfigured_llm_is_refused_before_the_turn(
    unset: str, guidance: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(unset, raising=False)
    monkeypatch.setattr(service, "_run_agent_turn", _turn_must_not_start)

    outcome = service.run_ask(_PROMPT, allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.ERROR
    assert outcome.exit_code is AskExitCode.ERROR
    assert outcome.error is not None
    assert outcome.error.message.startswith(guidance)
    assert "`opensre account login`" in outcome.error.message


@pytest.mark.usefixtures("custom_openai")
def test_signed_in_account_route_skips_the_local_provider_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("config.account.account_llm_route", _signed_in)
    monkeypatch.setattr(service, "_run_agent_turn", _answered)

    outcome = service.run_ask(_PROMPT, allowed_tools=(), bypass_approvals=False)

    assert outcome.status is AskStatus.SUCCESS
    assert outcome.response == "answer"


@pytest.mark.usefixtures("custom_openai")
def test_refused_turn_still_counts_as_a_not_configured_provider_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    analytics = _Analytics()
    monkeypatch.setattr(
        "infrastructure.analytics.prompt_log.sinks.posthog_ai.get_analytics", lambda: analytics
    )
    config = PromptLogConfig(log_path=tmp_path / "prompts.jsonl")
    monkeypatch.setattr(PromptLogConfig, "load", lambda: config)
    monkeypatch.setattr(service, "_run_agent_turn", _turn_must_not_start)

    outcome = service.run_ask(_PROMPT, allowed_tools=(), bypass_approvals=False)

    # The record a turn failing on its first LLM call leaves, so dashboards keep counting it.
    [generation] = [props for event, props in analytics.events if event == Event.AI_GENERATION]
    assert generation["error_kind"] == "action_agent_error"
    assert generation["ai_error_kind"] == "not_configured"
    assert generation["$ai_is_error"] is True
    assert generation["$ai_input"] == [{"role": "user", "content": _PROMPT}]
    assert outcome.error is not None
    assert generation["$ai_output_choices"] == [
        {"role": "assistant", "content": outcome.error.message}
    ]


@pytest.mark.usefixtures("custom_openai")
def test_turn_failing_on_a_missing_key_names_the_catalog_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: the client's label `Custom Openai` surfaced as `/auth login custom_openai`."""
    # The prompt-safe status still lists a key the request then cannot find.
    monkeypatch.setattr(service, "llm_ready", lambda: LLMReadiness(provider="custom-openai"))
    config = PromptLogConfig(log_path=tmp_path / "prompts.jsonl")
    monkeypatch.setattr(PromptLogConfig, "load", lambda: config)
    session = SessionCore(store=InMemorySessionStore())
    session.resolved_integrations_cache = {}

    def startup(_self: AgentSession) -> SessionStartupResult:
        return SessionStartupResult(session=session, prompts=None)

    client = OpenAIAgentClient(
        model="gateway-model",
        base_url="http://127.0.0.1:4000/v1",
        api_key_env="CUSTOM_OPENAI_API_KEY",
        credential_resolver=_static_key,
    )

    def build_agent(**kwargs: Any) -> action_driver.ActionTurnPlan:
        return action_driver.ActionTurnPlan(
            agent=_KeyVanishedAgent(),  # type: ignore[arg-type]
            user_message=kwargs["message"],
            llm=client,
            max_iterations=8,
        )

    monkeypatch.setattr(AgentSession, "startup", startup)
    monkeypatch.setattr(action_driver, "_build_action_agent", build_agent)

    outcome = service.run_ask(_PROMPT, allowed_tools=(), bypass_approvals=False, ephemeral=True)

    assert outcome.status is AskStatus.ERROR
    assert outcome.error is not None
    assert "`opensre auth login custom-openai`" in outcome.error.message
    assert "custom_openai" not in outcome.error.message
    # `opensre ask` cannot run a slash command.
    assert "`/" not in outcome.error.message
