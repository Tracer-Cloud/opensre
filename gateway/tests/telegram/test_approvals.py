"""Telegram inline-keyboard approval gate: prompter, hooks, callback routing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

from core.llm.types import ToolCall
from core.tool.execution import ToolExecutionRequest
from gateway.core.middleware.approvals import (
    APPROVE_ACTION_ID,
    DENY_ACTION_ID,
    ApprovalBroker,
    approval_tool_hooks,
)
from gateway.transports.telegram.approvals import (
    TelegramApprovalPrompter,
    handle_callback_query,
)
from gateway.transports.telegram.settings import TelegramCallbackQuery


class _FakeClient:
    def __init__(self, *, post_ok: bool = True) -> None:
        self.post_ok = post_ok
        self.posts: list[dict[str, Any]] = []
        self.edits: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.on_post: Callable[[], None] | None = None

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        parse_mode: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str, str]:
        self.posts.append(
            {
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "reply_markup": reply_markup,
            }
        )
        if not self.post_ok:
            return False, "fail", ""
        if self.on_post is not None:
            self.on_post()
        return True, "", f"msg-{len(self.posts)}"

    def edit_message_text(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        parse_mode: str = "",
        reply_markup: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        _ = parse_mode  # part of the real client signature; not asserted here
        self.edits.append(
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "reply_markup": reply_markup,
            }
        )
        return True, ""

    def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None:
        self.answers.append({"id": callback_query_id, "text": text})


class _FakeTool:
    def __init__(self, *, requires_approval: bool = True) -> None:
        self.requires_approval = requires_approval
        self.approval_reason = "Sends a Telegram message."
        self.approval_expiry_seconds = 60


def _request(tool: _FakeTool, name: str = "telegram_send_message") -> ToolExecutionRequest:
    return ToolExecutionRequest(
        tool_call=ToolCall(id="tc-1", name=name, input={"message": "hi"}),
        tool=tool,  # type: ignore[arg-type]
        arguments={"message": "hi"},
        source="telegram",
        resolved_integrations={},
    )


def _approval_id_from_markup(markup: dict[str, Any]) -> str:
    data = markup["inline_keyboard"][0][0]["callback_data"]
    return str(data).split(":", 1)[1]


def _prompter(client: _FakeClient, broker: ApprovalBroker) -> TelegramApprovalPrompter:
    return TelegramApprovalPrompter(client=client, broker=broker, chat_id="42")


def _click_on_post(
    broker: ApprovalBroker,
    client: _FakeClient,
    *,
    action_id: str,
    user_id: str = "42",
) -> None:
    """Deliver a click only once the prompt exists, without a timer race."""

    def _click() -> None:
        approval_id = _approval_id_from_markup(client.posts[-1]["reply_markup"])
        resolved = handle_callback_query(
            TelegramCallbackQuery(
                update_id=1,
                user_id=user_id,
                chat_id="42",
                callback_query_id="cq-1",
                data=f"{action_id}:{approval_id}",
            ),
            broker=broker,
            client=client,  # type: ignore[arg-type]
            allowed_user_ids=["42"],
        )
        assert resolved is True

    client.on_post = _click


def test_approved_click_lets_the_tool_run() -> None:
    broker = ApprovalBroker()
    client = _FakeClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_on_post(broker, client, action_id=APPROVE_ACTION_ID)

    result = hooks.before_tool_call(_request(_FakeTool()))
    assert result is not None
    assert result.approved is True
    assert client.edits  # outcome message cleared buttons


def test_denied_click_blocks_the_tool() -> None:
    broker = ApprovalBroker()
    client = _FakeClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    _click_on_post(broker, client, action_id=DENY_ACTION_ID)

    result = hooks.before_tool_call(_request(_FakeTool()))
    assert result is not None
    assert result.blocked is True
    assert "decision by <@42>" in result.reason


def test_failed_prompt_post_fails_closed() -> None:
    broker = ApprovalBroker()
    client = _FakeClient(post_ok=False)
    hooks = approval_tool_hooks(_prompter(client, broker))
    result = hooks.before_tool_call(_request(_FakeTool()))
    assert result is not None
    assert result.blocked is True


def test_empty_allowlist_rejects_click() -> None:
    broker = MagicMock()
    client = _FakeClient()
    ok = handle_callback_query(
        TelegramCallbackQuery(
            update_id=1,
            user_id="42",
            chat_id="42",
            callback_query_id="cq",
            data=f"{APPROVE_ACTION_ID}:appr-1",
        ),
        broker=broker,
        client=client,  # type: ignore[arg-type]
        allowed_user_ids=[],
    )
    assert ok is False
    broker.resolve.assert_not_called()


def test_unlisted_user_rejected() -> None:
    broker = MagicMock()
    client = _FakeClient()
    ok = handle_callback_query(
        TelegramCallbackQuery(
            update_id=1,
            user_id="999",
            chat_id="42",
            callback_query_id="cq",
            data=f"{APPROVE_ACTION_ID}:appr-1",
        ),
        broker=broker,
        client=client,  # type: ignore[arg-type]
        allowed_user_ids=["42"],
    )
    assert ok is False
    broker.resolve.assert_not_called()


def test_tools_without_requires_approval_skip_prompt() -> None:
    broker = ApprovalBroker()
    client = _FakeClient()
    hooks = approval_tool_hooks(_prompter(client, broker))
    result = hooks.before_tool_call(_request(_FakeTool(requires_approval=False)))
    assert result is None
    assert client.posts == []


def test_approval_callback_data_fits_telegram_limit() -> None:
    from gateway.transports.telegram.approvals import _approval_keyboard

    markup = _approval_keyboard("a" * 32)  # uuid.hex length
    for button in markup["inline_keyboard"][0]:
        assert len(button["callback_data"].encode("utf-8")) <= 64
