"""Active-turn cancel registry and /stop command parsing."""

from __future__ import annotations

import threading

from gateway.core.middleware.active_turns import (
    ActiveTurnRegistry,
    is_stop_command,
)


def test_is_stop_command_accepts_common_forms() -> None:
    assert is_stop_command("/stop")
    assert is_stop_command("stop")
    assert is_stop_command("/cancel")
    assert is_stop_command("  /stop@OpenSREBot please ")
    assert not is_stop_command("/status")
    assert not is_stop_command("please stop later")
    assert not is_stop_command("")


def test_request_stop_sets_event_and_runs_callback() -> None:
    registry = ActiveTurnRegistry()
    event = threading.Event()
    seen: list[str] = []

    with registry.track("chat-1", event, on_user_stop=lambda: seen.append("stop")):
        assert registry.request_stop("chat-1") is True
        assert event.is_set()
        assert seen == ["stop"]

    assert registry.request_stop("chat-1") is False


def test_track_unregisters_even_when_turn_raises() -> None:
    registry = ActiveTurnRegistry()
    event = threading.Event()
    try:
        with registry.track("chat-1", event):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert registry.request_stop("chat-1") is False


def test_request_stop_cancels_running_and_queued_turns_for_the_key() -> None:
    # Arrange — one running turn and one queued behind the conversation lock
    registry = ActiveTurnRegistry()
    running = threading.Event()
    queued = threading.Event()

    with registry.track("chat-1", running), registry.track("chat-1", queued):
        # Act
        stopped = registry.request_stop("chat-1")

        # Assert — /stop means stop the conversation, not just one turn
        assert stopped is True
        assert running.is_set()
        assert queued.is_set()


def test_stop_finds_a_turn_registered_at_dispatch_before_it_starts() -> None:
    # Arrange — dispatch registered the accepted turn; its task has not run yet
    registry = ActiveTurnRegistry()
    accepted = threading.Event()
    registry.register("chat-1", accepted)

    # Act
    stopped = registry.request_stop("chat-1")

    # Assert
    assert stopped is True
    assert accepted.is_set()
    registry.unregister("chat-1", accepted)
    assert registry.request_stop("chat-1") is False


def test_bind_user_stop_attaches_finalizer_to_a_registered_event() -> None:
    # Arrange
    registry = ActiveTurnRegistry()
    event = threading.Event()
    seen: list[str] = []
    registry.register("chat-1", event)

    # Act
    registry.bind_user_stop("chat-1", event, lambda: seen.append("stop"))
    registry.request_stop("chat-1")

    # Assert
    assert event.is_set()
    assert seen == ["stop"]
    registry.unregister("chat-1", event)
