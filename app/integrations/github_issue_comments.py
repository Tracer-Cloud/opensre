"""Notify Slack when GitHub issue comments are created."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

MAX_COMMENT_PREVIEW_CHARS = 500


@dataclass(frozen=True)
class IssueCommentNotification:
    """Normalized issue comment notification payload."""

    repository: str
    issue_number: int
    issue_title: str
    issue_url: str
    issue_author: str
    comment_author: str
    comment_url: str
    comment_body: str


def _string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _truncate_comment(text: str, *, limit: int = MAX_COMMENT_PREVIEW_CHARS) -> str:
    normalized = "\n".join(line.rstrip() for line in text.strip().splitlines()).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def notification_from_issue_comment_event(
    event: dict[str, Any],
    *,
    repository: str,
) -> IssueCommentNotification | None:
    """Build a Slack notification model from a GitHub issue_comment event."""
    issue = event.get("issue")
    comment = event.get("comment")
    if not isinstance(issue, dict) or not isinstance(comment, dict):
        return None

    # GitHub uses the same event for PR comments; ignore those here.
    if issue.get("pull_request"):
        return None

    issue_number = issue.get("number")
    if not isinstance(issue_number, int):
        return None

    issue_title = _string(issue.get("title"))
    issue_url = _string(issue.get("html_url"))
    comment_url = _string(comment.get("html_url"))
    comment_body = _string(comment.get("body"))
    issue_author = _string((issue.get("user") or {}).get("login"))
    comment_author = _string((comment.get("user") or {}).get("login"))

    if not all((repository, issue_title, issue_url, comment_url, comment_author)):
        return None

    return IssueCommentNotification(
        repository=repository,
        issue_number=issue_number,
        issue_title=issue_title,
        issue_url=issue_url,
        issue_author=issue_author or "unknown",
        comment_author=comment_author,
        comment_url=comment_url,
        comment_body=comment_body,
    )


def build_slack_payload(notification: IssueCommentNotification) -> dict[str, Any]:
    """Render a compact Slack incoming webhook payload."""
    comment_preview = _truncate_comment(notification.comment_body)
    issue_link = (
        f"<{notification.issue_url}|{notification.repository}#{notification.issue_number}>"
    )
    comment_link = f"<{notification.comment_url}|View comment>"

    preview_text = comment_preview or "_No comment body provided._"
    preview_block = "\n".join(f"> {line}" for line in preview_text.splitlines()) or "> "

    text = (
        f"New comment on {notification.repository}#{notification.issue_number} by "
        f"{notification.comment_author}: {notification.issue_title}"
    )

    return {
        "text": text,
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*New GitHub issue comment*\n"
                        f"{issue_link}: *{notification.issue_title}*"
                    ),
                },
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": (
                            f"*Commenter:* `{notification.comment_author}`"
                            f"    *Issue author:* `{notification.issue_author}`"
                            f"    {comment_link}"
                        ),
                    }
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": preview_block},
            },
        ],
    }


def send_slack_webhook(payload: dict[str, Any], webhook_url: str) -> None:
    """Send a payload to a Slack incoming webhook."""
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            status_code = getattr(response, "status", response.getcode())
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Slack webhook failed with HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Slack webhook failed: {exc.reason}") from exc

    if status_code >= 400:
        raise RuntimeError(f"Slack webhook failed with HTTP {status_code}")


def main() -> int:
    """Entrypoint used by the GitHub Actions workflow."""
    event_path = _string(os.getenv("GITHUB_EVENT_PATH"))
    repository = _string(os.getenv("GITHUB_REPOSITORY"))
    webhook_url = _string(
        os.getenv("SLACK_GITHUB_ISSUES_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_URL")
    )

    if not event_path:
        print("Missing GITHUB_EVENT_PATH.", file=sys.stderr)
        return 1
    if not repository:
        print("Missing GITHUB_REPOSITORY.", file=sys.stderr)
        return 1
    if not webhook_url:
        print("Skipped: Slack webhook is not configured.")
        return 0

    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    notification = notification_from_issue_comment_event(event, repository=repository)
    if notification is None:
        print("Skipped: event is not a normal issue comment.")
        return 0

    payload = build_slack_payload(notification)
    send_slack_webhook(payload, webhook_url)
    print(
        f"Posted Slack notification for {notification.repository}#{notification.issue_number}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
