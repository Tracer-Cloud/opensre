"""Unit tests for SlackWebApiClient without live Slack."""

from __future__ import annotations

from typing import Any

from slack_sdk.errors import SlackApiError

from gateway.transports.slack.client import SlackWebApiClient


class _FakeWebClient:
    def __init__(
        self, *, post: dict[str, Any] | Exception, update: Exception | None = None
    ) -> None:
        self._post = post
        self._update = update
        self.post_calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []

    def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
        self.post_calls.append(kwargs)
        if isinstance(self._post, Exception):
            raise self._post
        return self._post

    def chat_update(self, **kwargs: Any) -> dict[str, Any]:
        self.update_calls.append(kwargs)
        if self._update is not None:
            raise self._update
        return {"ok": True}


def _api_error(code: str) -> SlackApiError:
    response = {"ok": False, "error": code}
    return SlackApiError(message=code, response=response)


def test_post_message_returns_ts() -> None:
    web = _FakeWebClient(post={"ts": "1.2"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.post_message(channel="C1", text="hi", thread_ts="1.0") == "1.2"
    assert web.post_calls[0]["channel"] == "C1"


def test_post_message_returns_none_on_api_error() -> None:
    web = _FakeWebClient(post=_api_error("channel_not_found"))
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.post_message(channel="C1", text="hi") is None


def test_update_message_false_on_api_error() -> None:
    web = _FakeWebClient(post={"ts": "1.2"}, update=_api_error("message_not_found"))
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.update_message(channel="C1", ts="1.2", text="x") is False


def test_update_message_true_on_success() -> None:
    web = _FakeWebClient(post={"ts": "1.2"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.update_message(channel="C1", ts="1.2", text="done") is True


_MARKDOWN_BLOCKS = [{"type": "markdown", "text": "# hi"}]


def test_post_message_passes_blocks_through() -> None:
    web = _FakeWebClient(post={"ts": "1.2"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.post_message(channel="C1", text="hi", blocks=_MARKDOWN_BLOCKS) == "1.2"
    assert web.post_calls[0]["blocks"] == _MARKDOWN_BLOCKS


def test_post_message_retries_text_only_when_blocks_rejected() -> None:
    """A workspace that rejects the markdown block still gets the answer."""

    class _RecoveringWebClient(_FakeWebClient):
        def chat_postMessage(self, **kwargs: Any) -> dict[str, Any]:
            self.post_calls.append(kwargs)
            if "blocks" in kwargs:
                raise _api_error("invalid_blocks")
            return {"ts": "9.9"}

    web = _RecoveringWebClient(post={"ts": "unused"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.post_message(channel="C1", text="hi", blocks=_MARKDOWN_BLOCKS) == "9.9"
    assert "blocks" not in web.post_calls[-1]


def test_update_message_retries_text_only_when_blocks_rejected() -> None:
    class _RecoveringWebClient(_FakeWebClient):
        def chat_update(self, **kwargs: Any) -> dict[str, Any]:
            self.update_calls.append(kwargs)
            if "blocks" in kwargs:
                raise _api_error("invalid_blocks")
            return {"ok": True}

    web = _RecoveringWebClient(post={"ts": "1.2"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.update_message(channel="C1", ts="1.2", text="x", blocks=_MARKDOWN_BLOCKS) is True
    assert "blocks" not in web.update_calls[-1]


class _StreamingWebClient(_FakeWebClient):
    def __init__(self, *, start: dict[str, Any] | Exception) -> None:
        super().__init__(post={"ts": "unused"})
        self._start = start
        self.start_calls: list[dict[str, Any]] = []
        self.append_calls: list[dict[str, Any]] = []
        self.stop_calls: list[dict[str, Any]] = []

    def chat_startStream(self, **kwargs: Any) -> dict[str, Any]:
        self.start_calls.append(kwargs)
        if isinstance(self._start, Exception):
            raise self._start
        return self._start

    def chat_appendStream(self, **kwargs: Any) -> dict[str, Any]:
        self.append_calls.append(kwargs)
        return {"ok": True}

    def chat_stopStream(self, **kwargs: Any) -> dict[str, Any]:
        self.stop_calls.append(kwargs)
        return {"ok": True}


def test_start_stream_returns_ts_and_requests_timeline_mode() -> None:
    web = _StreamingWebClient(start={"ts": "5.5"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.start_stream(channel="C1", thread_ts="1.0") == "5.5"
    assert web.start_calls[0]["task_display_mode"] == "timeline"


def test_start_stream_caches_permanent_unsupported_error() -> None:
    web = _StreamingWebClient(start=_api_error("unknown_method"))
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.start_stream(channel="C1", thread_ts="1.0") is None
    assert client.start_stream(channel="C1", thread_ts="1.0") is None
    # Second call short-circuits without hitting the API again.
    assert len(web.start_calls) == 1


def test_start_stream_retries_after_transient_error() -> None:
    web = _StreamingWebClient(start=_api_error("internal_error"))
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert client.start_stream(channel="C1", thread_ts="1.0") is None
    assert client.start_stream(channel="C1", thread_ts="1.0") is None
    # Transient errors do not disable streaming for the process.
    assert len(web.start_calls) == 2


def test_stop_stream_retries_without_blocks_when_rejected() -> None:
    """Feedback buttons may be feature-gated; the stream must still stop."""

    class _BlockRejectingWebClient(_StreamingWebClient):
        def chat_stopStream(self, **kwargs: Any) -> dict[str, Any]:
            self.stop_calls.append(kwargs)
            if "blocks" in kwargs:
                raise _api_error("invalid_blocks")
            return {"ok": True}

    web = _BlockRejectingWebClient(start={"ts": "5.5"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    ok = client.stop_stream(channel="C1", ts="5.5", blocks=[{"type": "context_actions"}])

    assert ok is True
    assert "blocks" not in web.stop_calls[-1]


def test_set_thread_status_sets_loading_without_posting() -> None:
    class _StatusWebClient:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def assistant_threads_setStatus(self, **kwargs: Any) -> dict[str, Any]:
            self.calls.append(kwargs)
            return {"ok": True}

    web = _StatusWebClient()
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    assert (
        client.set_thread_status(
            channel="C1",
            thread_ts="1.0",
            status="is working on your request...",
            loading_messages=["Reading logs"],
        )
        is True
    )
    assert web.calls[0]["channel_id"] == "C1"
    assert web.calls[0]["thread_ts"] == "1.0"
    assert web.calls[0]["loading_messages"] == ["Reading logs"]
    assert client.set_thread_status(channel="C1", thread_ts="1.0", status="") is True
    assert web.calls[1]["status"] == ""
    assert "loading_messages" not in web.calls[1]


def test_append_and_stop_stream_round_trip() -> None:
    web = _StreamingWebClient(start={"ts": "5.5"})
    client = SlackWebApiClient(web)  # type: ignore[arg-type]

    chunks = [{"type": "markdown_text", "text": "hi"}]
    assert client.append_stream(channel="C1", ts="5.5", chunks=chunks) is True
    assert web.append_calls[0]["chunks"] == chunks
    footer = [{"type": "context", "elements": []}]
    assert client.stop_stream(channel="C1", ts="5.5", blocks=footer) is True
    assert web.stop_calls[0]["blocks"] == footer
