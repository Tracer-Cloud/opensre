"""The web-app GitHub menu opens the org home page and resumes the waiting skill."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

import surfaces.interactive_shell.command_registry.choice_prompt as choice_prompt
from config.constants.skills import (
    ANALYZE_REPO_OPTION,
    GITHUB_ONBOARDING_CONTINUE_OPTION,
    GITHUB_ONBOARDING_MENU_TITLE,
    GITHUB_ONBOARDING_OPEN_OPTION,
    ONBOARDING_MENU_TITLE,
    ONBOARDING_SKILL_NAME,
)
from core.agent_harness.session.pending_choice import PendingUserChoice
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.github_onboarding_gate import github_onboarding_action


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _pending() -> PendingUserChoice:
    return PendingUserChoice(
        title=GITHUB_ONBOARDING_MENU_TITLE,
        options=(GITHUB_ONBOARDING_OPEN_OPTION, GITHUB_ONBOARDING_CONTINUE_OPTION),
        note="Open https://app.opensre.com/home?org_id=org-1, connect GitHub, then continue.",
        commands={
            GITHUB_ONBOARDING_OPEN_OPTION: github_onboarding_action("open", ONBOARDING_SKILL_NAME),
            GITHUB_ONBOARDING_CONTINUE_OPTION: github_onboarding_action(
                "continue", ONBOARDING_SKILL_NAME
            ),
        },
        custom_answer=False,
    )


def test_continue_resumes_the_waiting_skill_once_github_is_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.pending_user_choice = _pending()
    console, _buf = _console()
    resumed: list[str] = []

    def _connected(**_kwargs: object) -> str:
        return "connected"

    def _enter(name: str, _scope: object) -> dict[str, object]:
        resumed.append(name)
        return {"ok": True, "name": name}

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(
        choice_prompt, "repl_choose_one", lambda **_kwargs: GITHUB_ONBOARDING_CONTINUE_OPTION
    )
    monkeypatch.setattr(
        "integrations.account_integrations.account_github_connection",
        _connected,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.github_setup_choice.account_github_connection",
        _connected,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.github_setup_choice.enter_skill",
        _enter,
    )

    assert choice_prompt._cmd_choose(session, console, []) is True
    assert resumed == [ONBOARDING_SKILL_NAME]
    assert session.pending_user_choice is None
    assert session.questions_already_answered == set()


def test_continue_opens_the_skill_menu_in_this_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The skill menu must render before ``/choose`` returns.

    A second autosubmitted ``/choose`` starts the prompt, which then blocks
    in a raw read, and the suspended prompt never reaches the menu.
    """
    session = Session()
    session.pending_user_choice = _pending()
    console, _buf = _console()
    titles: list[str] = []

    def _connected(**_kwargs: object) -> str:
        return "connected"

    def _enter(name: str, scope: object) -> dict[str, object]:
        target = getattr(scope, "session", None)
        assert target is session
        session.pending_user_choice = PendingUserChoice(
            title=ONBOARDING_MENU_TITLE,
            options=(ANALYZE_REPO_OPTION, "Open the shell"),
            custom_answer=False,
        )
        session.terminal.set_auto_command("/choose")
        return {"ok": True, "name": name}

    def _choose(**kwargs: object) -> str | None:
        title = kwargs.get("title")
        assert isinstance(title, str)
        titles.append(title)
        if title == GITHUB_ONBOARDING_MENU_TITLE:
            return GITHUB_ONBOARDING_CONTINUE_OPTION
        return ANALYZE_REPO_OPTION

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _choose)
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.github_setup_choice.account_github_connection",
        _connected,
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.github_setup_choice.enter_skill",
        _enter,
    )

    assert choice_prompt._cmd_choose(session, console, []) is True
    assert titles == [GITHUB_ONBOARDING_MENU_TITLE, ONBOARDING_MENU_TITLE]
    assert session.pending_user_choice is None
    queued = session.terminal.pending_prompt_default or ""
    assert queued != "/choose"
    assert ANALYZE_REPO_OPTION in queued


def test_continue_asks_again_when_github_is_still_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.pending_user_choice = _pending()
    console, buf = _console()
    notes: list[str] = []

    def _choose(**kwargs: object) -> str | None:
        note = kwargs.get("note")
        assert isinstance(note, str)
        notes.append(note)
        if len(notes) == 1:
            return GITHUB_ONBOARDING_CONTINUE_OPTION
        return None

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _choose)
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.github_setup_choice.account_github_connection",
        lambda **_kwargs: "missing",
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.github_setup_choice.github_onboarding_setup_url",
        lambda: "https://app.opensre.com/home?org_id=org-1",
    )

    assert choice_prompt._cmd_choose(session, console, []) is True
    assert len(notes) == 2
    assert "still not connected" in notes[1]
    assert session.terminal.pending_prompt_default != "/choose"
    assert "Running" not in buf.getvalue()


def test_open_launches_the_org_home_page_and_asks_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.pending_user_choice = _pending()
    console, buf = _console()
    opened: list[str] = []
    titles: list[str] = []

    def _choose(**kwargs: object) -> str | None:
        title = kwargs.get("title")
        assert isinstance(title, str)
        titles.append(title)
        if len(titles) == 1:
            return GITHUB_ONBOARDING_OPEN_OPTION
        return None

    def _open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _choose)
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.github_setup_choice.github_onboarding_setup_url",
        lambda: "https://app.opensre.com/home?org_id=org-1",
    )
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.github_setup_choice.webbrowser.open",
        _open,
    )

    assert choice_prompt._cmd_choose(session, console, []) is True
    assert opened == ["https://app.opensre.com/home?org_id=org-1"]
    assert titles == [GITHUB_ONBOARDING_MENU_TITLE, GITHUB_ONBOARDING_MENU_TITLE]
    assert session.terminal.pending_prompt_default != "/choose"
    assert "https://app.opensre.com/home?org_id=org-1" in buf.getvalue()
