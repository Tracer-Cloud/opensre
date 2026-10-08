"""The turn-error render adds ``/model`` and ``/auth login`` recovery hints on a credit-exhausted error."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.llm.shared.llm_retry import (
    LLMCreditExhaustedError,
    OpenSRECreditsExhaustedError,
)
from surfaces.interactive_shell.runtime import agent_presentation as ap
from surfaces.interactive_shell.runtime.agent_presentation import (
    AgentEvent,
    AgentPresentationState,
    ConsoleAgentEventSink,
    _render_agent_presentation_transition,
)
from surfaces.interactive_shell.session import Session


class _RecordingConsole:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, text: object = "") -> None:
        self.lines.append(text if isinstance(text, str) else getattr(text, "plain", str(text)))


def test_turn_complete_chimes_only_for_a_long_turn(monkeypatch) -> None:
    # A walk-away-length turn chimes on completion; a quick reply stays silent so
    # the shell does not beep on every short interactive turn.
    calls: list[object] = []
    monkeypatch.setattr(ap, "play_notification", lambda event: calls.append(event))
    clock = {"now": 1000.0}
    monkeypatch.setattr(ap.time, "monotonic", lambda: clock["now"])
    sink = ConsoleAgentEventSink(session=MagicMock(), spinner=MagicMock(), console=MagicMock())

    sink._turn_started_at = 1000.0
    clock["now"] = 1000.0 + ap.SOUND_MIN_TURN_SECONDS - 1
    sink._chime_if_long_turn()
    assert calls == []  # under the threshold: silent

    sink._turn_started_at = 1000.0
    clock["now"] = 1000.0 + ap.SOUND_MIN_TURN_SECONDS + 1
    sink._chime_if_long_turn()
    assert calls == [ap.NotifyEvent.TURN_COMPLETE]


@pytest.mark.parametrize(
    ("prompt_running", "exclusive_stdin", "drains"),
    [
        pytest.param(True, False, 0, id="live-prompt"),
        pytest.param(False, True, 1, id="exclusive-turn"),
        pytest.param(True, True, 1, id="exclusive-turn-with-a-lingering-app"),
    ],
)
def test_turn_end_drains_stdin_only_when_no_prompt_app_reads_it(
    monkeypatch: pytest.MonkeyPatch, prompt_running: bool, exclusive_stdin: bool, drains: int
) -> None:
    # A drain racing the live prompt app split the escape sequences it was
    # parsing; exclusive-stdin turns own the TTY and still drain after pickers.
    drained: list[bool] = []
    monkeypatch.setattr(ap, "drain_stale_cpr_bytes", lambda: drained.append(True))
    session = Session()
    session.terminal.prompt_app = SimpleNamespace(is_running=prompt_running)
    session.terminal.exclusive_stdin_active = exclusive_stdin
    sink = ConsoleAgentEventSink(session=session, spinner=MagicMock(), console=MagicMock())

    asyncio.run(sink(AgentEvent(type="turn_end")))

    assert len(drained) == drains


def _render_turn_error(error: Exception) -> str:
    console = _RecordingConsole()
    asyncio.run(
        _render_agent_presentation_transition(
            previous=AgentPresentationState(),
            current=AgentPresentationState(),
            event=AgentEvent(type="turn_error", error=error),
            console=console,  # type: ignore[arg-type]
            spinner=MagicMock(),
            drain_stdin=False,
        )
    )
    return "\n".join(console.lines)


def test_credit_exhausted_turn_error_shows_model_hint() -> None:
    output = _render_turn_error(LLMCreditExhaustedError("Anthropic credit exhausted"))
    assert "/model" in output


def test_credit_exhausted_turn_error_shows_auth_login_hint() -> None:
    output = _render_turn_error(LLMCreditExhaustedError("Anthropic credit exhausted"))
    assert "/auth login" in output


def test_opensre_credit_exhaustion_shows_checkout_instead_of_provider_hints() -> None:
    upgrade_url = "https://app.opensre.dev/usage"
    output = _render_turn_error(
        OpenSRECreditsExhaustedError(
            "OpenSRE credits exhausted",
            upgrade_url=upgrade_url,
        )
    )
    # Arrange/Act above. Assert: the URL appears once, as the way out; no provider hints.
    assert output.count(upgrade_url) == 1
    assert "/auth login" not in output
    assert "Top up or upgrade: " in output
    assert "/account usage" in output
    assert "/credits" in output


def test_opensre_credit_exhaustion_link_is_clickable_and_not_repeated() -> None:
    # Arrange: a real terminal-capable console so Rich emits OSC 8 hyperlinks.
    import io

    from rich.console import Console

    upgrade_url = "https://app.opensre.com/usage"
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=True, color_system="truecolor", width=200)

    # Act
    asyncio.run(
        _render_agent_presentation_transition(
            previous=AgentPresentationState(),
            current=AgentPresentationState(),
            event=AgentEvent(
                type="turn_error",
                error=OpenSRECreditsExhaustedError(
                    "OpenSRE credit exhausted (provider billing/quota). Your hosted credits "
                    f"are exhausted. Upgrade or top up at {upgrade_url}.",
                    upgrade_url=upgrade_url,
                ),
            ),
            console=console,  # type: ignore[arg-type]
            spinner=MagicMock(),
            drain_stdin=False,
        )
    )

    # Assert: the URL is wrapped in a terminal hyperlink and printed once.
    out = buffer.getvalue()
    link_start = out.index("\x1b]8;")
    link_target = out[link_start : out.index("\x1b\\", link_start)]
    assert link_target.endswith(upgrade_url)
    assert out.count(upgrade_url) == 2  # once inside the OSC 8 target, once as visible text


def test_other_turn_error_has_no_model_hint() -> None:
    output = _render_turn_error(RuntimeError("something else broke"))
    assert "/model" not in output
    assert "/auth login" not in output
