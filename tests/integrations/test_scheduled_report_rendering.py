"""A loop's Markdown report reads natively in Slack, Telegram and email."""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import pytest

from infrastructure.delivery.notifications.delivery_transport import DeliveryResponse
from infrastructure.scheduling.scheduler.report_style import report_style_block
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from infrastructure.text.report_layout import parse_report
from integrations.manual_loop_runner import build_manual_loop_prompt
from integrations.slack.report_blocks import report_blocks
from integrations.slack.scheduled_delivery import SlackScheduledDelivery
from integrations.smtp.report_email import render_report_email
from integrations.telegram.report_format import telegram_report_html

_LOOP = "Weekday engineering morning report"
_REPORT = """# ☀️ Engineering morning report · Mon 19 Oct
Main is green; **2 PRs** have waited **13 days** for review.

## 🔴 Needs attention
- **@davincios** — review [#6611 hourly cron](https://github.com/o/r/pull/6611) · waiting 13d
  - requested 5 Oct

## ✅ CI
- **Main CI:** 79 runs, **0 failures**

_Coverage: Discord history not readable_
"""


def _texts(blocks: list[dict[str, Any]]) -> str:
    out = []
    for block in blocks:
        if block["type"] == "context":
            out.extend(element["text"] for element in block["elements"])
        elif "text" in block:
            out.append(block["text"]["text"])
    return "\n".join(out)


def test_a_report_splits_into_title_lead_sections_and_a_closing_note() -> None:
    # Act
    layout = parse_report(_REPORT)

    # Assert
    assert layout.title == "☀️ Engineering morning report · Mon 19 Oct"
    assert layout.summary().startswith("Main is green")
    assert [section.heading for section in layout.sections] == ["🔴 Needs attention", "✅ CI"]
    assert layout.note == "Coverage: Discord history not readable"
    nested = layout.sections[0].lines[1]
    assert (nested.bullet, nested.depth) == (True, 1)


def test_bold_only_lines_are_read_as_headings_for_replies_written_the_old_way() -> None:
    # Arrange: how loop replies looked before the format guide.
    old = "**Needs Action**\n- one\n\n**GitHub:**\n- two"

    # Act
    layout = parse_report(old)

    # Assert
    assert [section.heading for section in layout.sections] == ["Needs Action", "GitHub"]


def test_slack_gets_block_kit_with_no_markdown_left_in_it() -> None:
    # Act
    fallback, blocks = report_blocks(_REPORT, loop_name=_LOOP)

    # Assert: a header, divided sections, the note and the loop as quiet context.
    assert blocks[0] == {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": "☀️ Engineering morning report · Mon 19 Oct",
            "emoji": True,
        },
    }
    assert [block["type"] for block in blocks].count("divider") == 2
    rendered = _texts(blocks)
    assert "**" not in rendered and "](" not in rendered
    assert "<https://github.com/o/r/pull/6611|#6611 hourly cron>" in rendered
    assert "*🔴 Needs attention*" in rendered
    assert blocks[-2]["elements"][0]["text"] == "_Coverage: Discord history not readable_"
    assert blocks[-1]["elements"][0]["text"] == f"{_LOOP} · scheduled by OpenSRE"
    assert fallback.startswith("☀️ Engineering morning report · Mon 19 Oct: Main is green")


def test_slack_splits_a_long_section_under_its_block_limit() -> None:
    # Arrange: one section far over Slack's 3,000 characters.
    long_report = "## Items\n" + "\n".join(f"- item {index} " + "x" * 60 for index in range(120))

    # Act
    _fallback, blocks = report_blocks(long_report, loop_name=_LOOP)

    # Assert
    sections = [block for block in blocks if block["type"] == "section"]
    assert len(sections) > 1
    assert all(len(block["text"]["text"]) <= 3000 for block in sections)


def test_a_loop_report_posts_as_blocks_without_link_previews(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "integrations.slack.scheduled_delivery.resolve_slack_credentials",
        lambda _params: {"access_token": "xoxb-1"},
    )

    def post_json(
        *, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> DeliveryResponse:
        sent.append(payload)
        return DeliveryResponse(ok=True, status_code=HTTPStatus.OK, data={"ok": True, "ts": "1.2"})

    monkeypatch.setattr("integrations.slack.scheduled_delivery.post_json", post_json)
    task = ScheduledTask(
        id="loop1",
        name=_LOOP,
        kind=TaskKind.MANUAL_LOOP,
        cron="0 9 * * 1-5",
        provider=Provider.SLACK,
        chat_id="C123",
    )

    # Act
    ok, _error, message_id = SlackScheduledDelivery().deliver(task, _REPORT)

    # Assert
    assert (ok, message_id) == (True, "1.2")
    [payload] = sent
    assert payload["channel"] == "C123"
    assert payload["blocks"][0]["type"] == "header"
    assert payload["unfurl_links"] is False and payload["unfurl_media"] is False


def test_telegram_spells_out_the_shape_in_its_html() -> None:
    # Act
    html = telegram_report_html(_REPORT, loop_name=_LOOP)

    # Assert
    assert html.startswith("<b>☀️ Engineering morning report · Mon 19 Oct</b>")
    assert "<b>🔴 Needs attention</b>" in html
    assert '<a href="https://github.com/o/r/pull/6611">#6611 hourly cron</a>' in html
    assert "**" not in html and "##" not in html


def test_email_has_a_subject_an_html_body_and_a_plain_text_part() -> None:
    # Act
    email = render_report_email(_REPORT, loop_name=_LOOP)

    # Assert
    assert email.subject == "☀️ Engineering morning report · Mon 19 Oct"
    assert '<a href="https://github.com/o/r/pull/6611"' in email.html
    assert "<strong>@davincios</strong>" in email.html
    assert "<em>Coverage: Discord history not readable</em>" not in email.html  # note, not italics
    assert "Coverage: Discord history not readable" in email.html
    assert "#6611 hourly cron (https://github.com/o/r/pull/6611)" in email.text
    assert "**" not in email.text and "<" not in email.text


def test_the_guide_names_the_destination_and_its_length() -> None:
    # Act
    block = report_style_block(["slack", "email"], timezone="Europe/London")

    # Assert: the tightest destination sets the budget.
    assert "delivered to Slack, email" in block
    assert "under about 1800 characters" in block
    assert "Europe/London" in block


@pytest.mark.parametrize("mode", ["report", "agent"])
def test_every_loop_tick_carries_the_reply_format(mode: str) -> None:
    # Arrange
    payload = {
        "name": _LOOP,
        "loop_prompt": "Summarize main CI and open PRs.",
        "loop_mode": mode,
        "loop_channels": "telegram",
        "timezone": "UTC",
    }

    # Act
    prompt = build_manual_loop_prompt(payload)

    # Assert
    assert "REPLY FORMAT" in prompt
    assert "delivered to Telegram" in prompt
    assert prompt.index("REPLY FORMAT") < prompt.index("Summarize main CI")


def test_a_bold_statement_with_nothing_under_it_is_kept_as_content() -> None:
    # Arrange: a closing bold sentence, and a real heading left empty.
    report = "## CI\n- **Main CI:** green\n\n**Deploys all health-checked.**\n\n## Empty"

    # Act
    layout = parse_report(report)
    _fallback, blocks = report_blocks(report, loop_name=_LOOP)

    # Assert
    assert [section.heading for section in layout.sections] == ["CI"]
    assert layout.sections[0].lines[-1].text == "**Deploys all health-checked.**"
    assert "*Deploys all health-checked.*" in _texts(blocks)
