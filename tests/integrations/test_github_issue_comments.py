from __future__ import annotations

import json

from app.integrations.github_issue_comments import (
    build_slack_payload,
    main,
    notification_from_issue_comment_event,
)


def _issue_comment_event(*, pull_request: bool = False, body: str = "Looks good to me.") -> dict:
    issue: dict[str, object] = {
        "number": 42,
        "title": "Add GitHub issue comment notifications",
        "html_url": "https://github.com/Tracer-Cloud/opensre/issues/42",
        "user": {"login": "issue-author"},
    }
    if pull_request:
        issue["pull_request"] = {"url": "https://api.github.com/repos/foo/bar/pulls/42"}

    return {
        "action": "created",
        "issue": issue,
        "comment": {
            "html_url": "https://github.com/Tracer-Cloud/opensre/issues/42#issuecomment-1",
            "body": body,
            "user": {"login": "comment-author"},
        },
    }


def test_notification_from_issue_comment_event_extracts_fields() -> None:
    notification = notification_from_issue_comment_event(
        _issue_comment_event(),
        repository="Tracer-Cloud/opensre",
    )

    assert notification is not None
    assert notification.repository == "Tracer-Cloud/opensre"
    assert notification.issue_number == 42
    assert notification.comment_author == "comment-author"
    assert notification.issue_author == "issue-author"


def test_notification_from_issue_comment_event_ignores_pull_request_comments() -> None:
    notification = notification_from_issue_comment_event(
        _issue_comment_event(pull_request=True),
        repository="Tracer-Cloud/opensre",
    )

    assert notification is None


def test_build_slack_payload_truncates_long_comment_preview() -> None:
    notification = notification_from_issue_comment_event(
        _issue_comment_event(body="A" * 700),
        repository="Tracer-Cloud/opensre",
    )
    assert notification is not None

    payload = build_slack_payload(notification)

    assert "New comment on Tracer-Cloud/opensre#42" in payload["text"]
    preview_block = payload["blocks"][2]["text"]["text"]
    assert preview_block.startswith("> ")
    assert preview_block.endswith("...")


def test_main_posts_notification(tmp_path, monkeypatch) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_issue_comment_event()), encoding="utf-8")

    captured: dict[str, object] = {}

    def _fake_send(payload: dict[str, object], webhook_url: str) -> None:
        captured["payload"] = payload
        captured["webhook_url"] = webhook_url

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "Tracer-Cloud/opensre")
    monkeypatch.setenv("SLACK_GITHUB_ISSUES_WEBHOOK_URL", "https://hooks.slack.test/abc")
    monkeypatch.setattr(
        "app.integrations.github_issue_comments.send_slack_webhook",
        _fake_send,
    )

    exit_code = main()

    assert exit_code == 0
    assert captured["webhook_url"] == "https://hooks.slack.test/abc"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "New comment on Tracer-Cloud/opensre#42" in payload["text"]


def test_main_skips_when_webhook_is_not_configured(tmp_path, monkeypatch, capsys) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_issue_comment_event()), encoding="utf-8")

    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_REPOSITORY", "Tracer-Cloud/opensre")
    monkeypatch.delenv("SLACK_GITHUB_ISSUES_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)

    exit_code = main()

    assert exit_code == 0
    assert "Skipped: Slack webhook is not configured." in capsys.readouterr().out
