"""After a hosted credit wall the shell queues a ways-out menu; chat sessions get none."""

from __future__ import annotations

from core.agent_harness.session.session_core import SessionCore
from surfaces.interactive_shell.runtime import credit_wall
from surfaces.interactive_shell.session import Session


def test_shell_session_gets_the_menu_with_slash_commands_as_answers() -> None:
    # Arrange
    session = Session()

    # Act
    credit_wall.queue_credits_exhausted_menu(session)

    # Assert: each option maps to a slash command, so no answer fires a model turn.
    pending = session.pending_user_choice
    assert pending is not None
    assert pending.title == credit_wall.CREDITS_MENU_TITLE
    assert pending.commands == {
        credit_wall.CREDITS_OPTION_TOP_UP: "/account usage",
        credit_wall.CREDITS_OPTION_SWITCH: "/model",
    }
    assert session.terminal.pending_prompt_default == "/choose"


def test_headless_session_gets_no_menu() -> None:
    # Arrange: a chat-transport session has no terminal facet.
    session = SessionCore()

    # Act
    credit_wall.queue_credits_exhausted_menu(session)

    # Assert
    assert session.pending_user_choice is None
