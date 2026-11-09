"""Developer journey: create and drive your own agent on the Python API.

Pins the documented ladder in ``docs/guides/python-api.mdx`` — two-line start, custom
sink, custom prompt-context provider, multi-turn reuse — without a live LLM provider.
"""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_harness.harness import AgentSession, SessionConfig
from core.agent_harness.turns.headless_adapters import (
    BufferOutputSink,
    EmptyPromptContextProvider,
    NullToolProvider,
)
from core.agent_harness.turns.headless_agent import HeadlessAgent
from core.agent_harness.turns.headless_build import DefaultHeadlessBuild, InMemoryHeadlessBuild
from core.agent_harness.turns.turn_results import ToolCallingTurnResult, TurnResult


class _CallerPrompts(EmptyPromptContextProvider):
    """A caller's own prompt-context provider; the agent must keep it."""


def _headless_config(**overrides: Any) -> SessionConfig:
    """No env / storage / integrations — embedder unit-test shape."""
    return SessionConfig(
        load_env=False,
        hydrate_integrations=False,
        persistent_tasks=False,
        open_store=False,
        **overrides,
    )


def _unhandled_actions(*_args: Any, **_kwargs: Any) -> ToolCallingTurnResult:
    """Return one deterministic agent answer."""
    return ToolCallingTurnResult(
        planned_count=0,
        executed_count=0,
        executed_success_count=0,
        has_unhandled_clause=False,
        handled=False,
        response_text="echo:ok",
    )


@pytest.fixture
def stub_action_planner(monkeypatch: Any) -> None:
    """Keep the agent off the network while preserving output behavior."""

    def _run(runner: Any, *_args: Any, **_kwargs: Any) -> ToolCallingTurnResult:
        runner.output.stream(label="OpenSRE", chunks=iter(["echo:ok"]))
        return _unhandled_actions()

    monkeypatch.setattr("core.agent_harness.turns.headless_agent.ActionTurnRunner.run", _run)


def _controlled_agent(
    *,
    output: Any | None = None,
    prompts: Any | None = None,
) -> tuple[HeadlessAgent, Any]:
    sink = output if output is not None else BufferOutputSink()
    agent = InMemoryHeadlessBuild(output=sink).agent(
        tools=NullToolProvider(),
        prompts=prompts if prompts is not None else EmptyPromptContextProvider(),
    )
    return agent, sink


def test_two_line_api_dispatches_an_answer(stub_action_planner: None) -> None:
    """``AgentSession`` + ``chat`` is the documented happy path."""
    # Arrange
    harness = AgentSession(_headless_config())
    agent, _sink = _controlled_agent()
    harness.attach_agent(agent)

    # Act
    result = harness.chat("why is checkout-api slow?")

    # Assert
    assert isinstance(result, TurnResult)
    assert result.answered
    assert "echo:" in (result.primary_response_text or "")


def test_follow_up_reuses_the_same_attached_agent(stub_action_planner: None) -> None:
    """Each ``chat`` is one turn on the same agent/session."""
    # Arrange
    harness = AgentSession(_headless_config())
    agent, _sink = _controlled_agent()
    harness.attach_agent(agent)

    # Act
    first = harness.chat("list open incidents")
    second = harness.chat("which affect checkout?")

    # Assert
    assert harness.agent is agent
    assert first.answered and second.answered
    assert first.primary_response_text != ""
    assert second.primary_response_text != ""


def test_documented_custom_sink_path_captures_streamed_answer(
    stub_action_planner: None,
) -> None:
    """``startup`` → ``DefaultHeadlessBuild(...).agent()`` → ``attach_agent``."""
    # Arrange
    harness = AgentSession(_headless_config())
    startup = harness.startup()
    sink = BufferOutputSink()
    agent = DefaultHeadlessBuild(session=startup.session, output=sink).agent()
    agent._tools = NullToolProvider()  # noqa: SLF001
    harness.attach_agent(agent)

    # Act
    result = harness.chat("summarize open incidents")

    # Assert
    assert result.answered
    assert sink.streamed, "BufferOutputSink.streamed must collect answer chunks"
    assert any("echo:" in chunk for chunk in sink.streamed)


def test_start_honours_caller_prompt_provider(stub_action_planner: None) -> None:
    """``SessionConfig.prompts`` remains bound to the agent."""
    # Arrange
    prompts = _CallerPrompts()
    harness = AgentSession.start(_headless_config(prompts=prompts))
    assert harness.agent is not None
    harness.agent._tools = NullToolProvider()  # noqa: SLF001

    # Act
    result = harness.chat("what is our on-call policy?")

    # Assert
    assert harness.agent._prompts is prompts  # noqa: SLF001
    assert result.answered
    assert "echo:ok" in (result.primary_response_text or "")


def test_builder_accepts_caller_prompt_provider_on_the_second_path() -> None:
    """The explicit ``DefaultHeadlessBuild.agent`` path must take ``prompts=``."""
    # Arrange
    harness = AgentSession(_headless_config())
    startup = harness.startup()
    prompts = _CallerPrompts()
    sink = BufferOutputSink()

    # Act
    agent = DefaultHeadlessBuild(session=startup.session, output=sink).agent(prompts=prompts)

    # Assert — wired before any dispatch
    assert agent._prompts is prompts  # noqa: SLF001


def test_custom_output_sink_protocol_is_enough(stub_action_planner: None) -> None:
    """Any ``OutputSink`` works — not only ``BufferOutputSink``."""

    class _ListSink:
        def __init__(self) -> None:
            self.lines: list[str] = []
            self.streamed: list[str] = []

        def print(self, message: str = "") -> None:
            self.lines.append(message)

        def render_response_header(self, label: str) -> None:
            self.lines.append(f"[{label}]")

        def render_error(self, message: str) -> None:
            self.lines.append(f"ERROR: {message}")

        def stream(
            self,
            *,
            label: str,
            chunks: Any,
            suppress_if_starts_with: str | None = None,
            defer_want_me_to_closer: bool = False,
        ) -> str:
            _ = (label, suppress_if_starts_with, defer_want_me_to_closer)
            text = "".join(str(c) for c in chunks)
            self.streamed.append(text)
            return text

    # Arrange
    sink = _ListSink()
    harness = AgentSession(_headless_config())
    agent, _ = _controlled_agent(output=sink)
    harness.attach_agent(agent)

    # Act
    result = harness.chat("ping")

    # Assert
    assert result.answered
    assert sink.streamed


def test_start_without_prompts_keeps_the_default_provider() -> None:
    """Omitting ``prompts`` binds the default provider, not none."""
    # Arrange / Act
    harness = AgentSession.start(_headless_config())

    # Assert
    assert harness.agent is not None
    assert type(harness.agent._prompts).__name__ == "DefaultPromptContextProvider"  # noqa: SLF001


def test_resume_config_reaches_session_manager() -> None:
    """``SessionConfig.session_id`` is how a developer resumes a conversation."""
    # Arrange
    from surfaces.interactive_shell.session import Session

    class _FakeSessionManager:
        def __init__(self) -> None:
            self.session = Session()
            self.resolve_calls: list[dict[str, Any]] = []

        def create(self, **_kwargs: Any) -> Session:
            raise AssertionError("resume must call resolve, not create")

        def resolve(self, session_id: str, **kwargs: Any) -> Session:
            self.resolve_calls.append({"session_id": session_id, **kwargs})
            return self.session

    manager = _FakeSessionManager()
    harness = AgentSession(
        SessionConfig(
            session_id="abc123",
            load_env=False,
            hydrate_integrations=False,
            persistent_tasks=False,
            open_store=False,
            session_manager=manager,  # type: ignore[arg-type]
        )
    )

    # Act
    startup = harness.startup()

    # Assert
    assert startup.session is manager.session
    assert manager.resolve_calls[0]["session_id"] == "abc123"


def test_every_advertised_name_is_a_plain_static_import() -> None:
    """Each API module's ``__all__`` names something bound at module level.

    A plain re-export is visible to type checkers, IDEs, and readers alike; a
    name that is only resolvable at runtime is not part of the API.
    """
    import ast
    import importlib
    from pathlib import Path

    import core.agent_harness as root
    import core.agent_harness.ports as ports
    import core.agent_harness.runtime as runtime

    roles = [
        importlib.import_module(f"core.agent_harness.spi.{r}")
        for r in (
            "session_goal",
            "session_state",
            "cancel",
            "accounting",
            "prompt_chrome",
            "integrations",
            "grounding",
            "defaults",
        )
    ]
    for api_module in (root, ports, runtime, *roles):
        tree = ast.parse(Path(api_module.__file__).read_text(encoding="utf-8"))
        bound: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                bound.update(alias.asname or alias.name for alias in node.names)
            elif isinstance(node, ast.ClassDef | ast.FunctionDef):
                bound.add(node.name)
            elif isinstance(node, ast.Assign):
                bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                bound.add(node.target.id)
        assert set(api_module.__all__) <= bound, (
            f"{api_module.__name__}: exported but not bound at module level: "
            f"{sorted(set(api_module.__all__) - bound)}"
        )
