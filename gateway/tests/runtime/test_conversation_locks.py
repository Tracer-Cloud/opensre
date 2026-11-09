"""Concurrency contracts for the shared conversation lock registry."""

from __future__ import annotations

import threading

import pytest

from gateway.core.middleware.conversation_locks import ConversationLockRegistry

_THREAD_TIMEOUT_SECONDS = 1.0
_BLOCKED_CHECK_SECONDS = 0.05


def test_different_conversations_can_run_concurrently() -> None:
    registry = ConversationLockRegistry()
    entered = threading.Event()

    def hold_other_conversation() -> None:
        with registry.hold("other"):
            entered.set()

    with registry.hold("busy"):
        worker = threading.Thread(target=hold_other_conversation)
        worker.start()
        assert entered.wait(_THREAD_TIMEOUT_SECONDS)

    worker.join(_THREAD_TIMEOUT_SECONDS)
    assert not worker.is_alive()


def test_active_conversation_survives_pruning_and_serializes_waiter() -> None:
    registry = ConversationLockRegistry(max_entries=1)
    attempting = threading.Event()
    entered = threading.Event()

    def wait_for_busy_conversation() -> None:
        attempting.set()
        with registry.hold("busy"):
            entered.set()

    with registry.hold("busy"):
        # A new conversation at capacity triggers pruning while busy is active.
        with registry.hold("other"):
            pass

        worker = threading.Thread(target=wait_for_busy_conversation)
        worker.start()
        assert attempting.wait(_THREAD_TIMEOUT_SECONDS)
        assert not entered.wait(_BLOCKED_CHECK_SECONDS)

    assert entered.wait(_THREAD_TIMEOUT_SECONDS)
    worker.join(_THREAD_TIMEOUT_SECONDS)
    assert not worker.is_alive()


def test_idle_entries_are_pruned_at_capacity() -> None:
    registry = ConversationLockRegistry(max_entries=2)

    for index in range(10):
        with registry.hold(f"conversation-{index}"):
            pass

    assert len(registry) <= 2


def _fail_turn() -> None:
    raise RuntimeError("turn failed")


def test_exception_releases_conversation_for_next_turn() -> None:
    registry = ConversationLockRegistry()

    with pytest.raises(RuntimeError, match="turn failed"), registry.hold("conversation"):
        _fail_turn()

    with registry.hold("conversation"):
        pass
