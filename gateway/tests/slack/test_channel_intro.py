"""First-join channel intro greeter."""

from __future__ import annotations

from typing import Any

from gateway.transports.slack.delivery.channel_intro import ChannelIntroGreeter


class _FakeMessagingClient:
    def __init__(self, *, post_ok: bool = True) -> None:
        self.post_ok = post_ok
        self.posts: list[dict[str, Any]] = []

    def post_message(
        self,
        *,
        channel: str,
        text: str,
        thread_ts: str | None = None,
        blocks: Any = None,
    ) -> str | None:
        self.posts.append(
            {"channel": channel, "text": text, "thread_ts": thread_ts, "blocks": blocks}
        )
        return f"ts-{len(self.posts)}" if self.post_ok else None


def _join_payload(*, user: str = "UBOT", channel: str = "C1") -> dict[str, Any]:
    return {"event": {"type": "member_joined_channel", "user": user, "channel": channel}}


def _greeter(client: _FakeMessagingClient) -> ChannelIntroGreeter:
    return ChannelIntroGreeter(messaging=client, bot_user_id="UBOT")


def test_bot_join_posts_intro_to_the_channel() -> None:
    client = _FakeMessagingClient()

    assert _greeter(client).handle(_join_payload()) is True

    post = client.posts[0]
    assert post["channel"] == "C1"
    assert post["thread_ts"] is None
    # The intro teaches the three behaviors people otherwise discover by
    # trial and error, using the real mention token.
    assert "<@UBOT>" in post["text"]
    assert "follow the conversation" in post["text"]
    assert "High-impact actions" in post["text"]


def test_rejoining_the_same_channel_greets_once() -> None:
    client = _FakeMessagingClient()
    greeter = _greeter(client)

    assert greeter.handle(_join_payload()) is True
    assert greeter.handle(_join_payload()) is False
    assert len(client.posts) == 1


def test_each_channel_gets_its_own_intro() -> None:
    client = _FakeMessagingClient()
    greeter = _greeter(client)

    assert greeter.handle(_join_payload(channel="C1")) is True
    assert greeter.handle(_join_payload(channel="C2")) is True
    assert [p["channel"] for p in client.posts] == ["C1", "C2"]


def test_other_members_joining_stay_silent() -> None:
    client = _FakeMessagingClient()

    assert _greeter(client).handle(_join_payload(user="UHUMAN")) is False
    assert not client.posts


def test_non_join_events_stay_silent() -> None:
    client = _FakeMessagingClient()

    payload = {"event": {"type": "message", "user": "UBOT", "channel": "C1"}}
    assert _greeter(client).handle(payload) is False
    assert not client.posts


def test_missing_bot_user_id_never_greets() -> None:
    client = _FakeMessagingClient()
    greeter = ChannelIntroGreeter(messaging=client, bot_user_id="")

    assert greeter.handle(_join_payload(user="")) is False
    assert not client.posts


def test_failed_post_allows_retry_on_next_join() -> None:
    client = _FakeMessagingClient(post_ok=False)
    greeter = _greeter(client)

    assert greeter.handle(_join_payload()) is False
    client.post_ok = True
    assert greeter.handle(_join_payload()) is True
