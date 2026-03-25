from __future__ import annotations

import httpx

from app.agent.utils.slack_delivery import send_slack_report


def test_send_slack_report_uses_incoming_webhook_without_thread_ts(monkeypatch) -> None:
    captured: dict = {}

    def _fake_post(url: str, json: dict, timeout: float, follow_redirects: bool):  # noqa: ANN001
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        captured["follow_redirects"] = follow_redirects
        return httpx.Response(200, request=httpx.Request("POST", url), text="ok")

    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/test/webhook")
    monkeypatch.setattr("app.agent.utils.slack_delivery.httpx.post", _fake_post)

    success, error = send_slack_report(
        "RCA report text",
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": "hello"}}],
    )

    assert success is True
    assert error == ""
    assert captured["url"] == "https://hooks.slack.com/services/test/webhook"
    assert captured["json"]["text"] == "RCA report text"
    assert captured["json"]["blocks"][0]["type"] == "section"


def test_send_slack_report_without_thread_or_webhook_returns_error(monkeypatch) -> None:
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    success, error = send_slack_report("RCA report text")

    assert success is False
    assert error == "no_thread_ts"
