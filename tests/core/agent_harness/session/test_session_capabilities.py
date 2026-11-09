"""A host states what it withholds; the harness owns how that is recorded."""

from __future__ import annotations

from core.agent_harness import SessionCore
from core.agent_harness.spi.session_state import withhold_capabilities


def test_withheld_capabilities_offer_no_tools() -> None:
    # Arrange: a session where one capability already offers a tool.
    session = SessionCore()
    session.available_capabilities["shell_commands"] = ("shell",)

    # Act
    withhold_capabilities(session, "investigation", "task_cancel")

    # Assert: the named capabilities offer nothing; the others are untouched.
    assert session.available_capabilities["investigation"] == ()
    assert session.available_capabilities["task_cancel"] == ()
    assert session.available_capabilities["shell_commands"] == ("shell",)


def test_withholding_twice_is_the_same_as_once() -> None:
    """Hosts state the policy wherever a session is prepared, without coordinating."""
    # Arrange
    session = SessionCore()

    # Act
    withhold_capabilities(session, "investigation")
    withhold_capabilities(session, "investigation")

    # Assert
    assert session.available_capabilities == {"investigation": ()}
