"""The schedule offer refuses a destination the scheduler could not reach.

A real install accumulated 37 Slack ``daily_summary`` rows with an empty
``chat_id`` and no webhook to fall back on. ``/cron add`` rejects that shape via
``requires_explicit_chat_id``; this tool hard-coded Slack as exempt and did not.
"""

from __future__ import annotations

from typing import Any

import tools.interactive_shell.actions.propose_scheduled_delivery as propose


class _Session:
    history: list[dict[str, Any]] = []


class _Ctx:
    session = _Session()
    history_start = 0


def _offer(**overrides: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "kind": "github_pr_sweep",
        "cron": "0 8 * * 1-5",
        "timezone": "UTC",
        "provider": "slack",
        "chat_id": "",
    }
    args.update(overrides)
    return propose.execute_propose_scheduled_delivery_tool(args, _Ctx())


def test_slack_without_a_channel_or_webhook_is_refused(monkeypatch) -> None:
    # Arrange: no webhook configured, so Slack has no destination to fall back
    # on and the task would fire into nothing.
    monkeypatch.setattr(propose, "requires_explicit_chat_id", lambda _provider: True)

    # Act
    result = _offer()

    # Assert
    assert result["ok"] is False
    assert "chat" in str(result["error"]).lower()


def test_slack_with_a_webhook_still_needs_no_channel(monkeypatch) -> None:
    # Arrange: a webhook carries its own destination — demanding a chat_id here
    # would break the channel-bound flow that legitimately has none.
    monkeypatch.setattr(propose, "requires_explicit_chat_id", lambda _provider: False)

    # Act
    result = _offer()

    # Assert
    assert result["ok"] is not False


def test_slack_with_an_explicit_channel_is_accepted(monkeypatch) -> None:
    # Arrange
    monkeypatch.setattr(propose, "requires_explicit_chat_id", lambda _provider: True)

    # Act
    result = _offer(chat_id="C0123ABCD")

    # Assert
    assert result["ok"] is not False
