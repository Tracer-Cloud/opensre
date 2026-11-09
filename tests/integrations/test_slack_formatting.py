"""Tests for Markdown → Slack mrkdwn conversion."""

from __future__ import annotations

from integrations.slack.formatting import markdown_to_slack_mrkdwn as convert


def test_bold_double_asterisk_becomes_single() -> None:
    assert convert("**I found:** ok") == "*I found:* ok"
    assert convert("__also bold__") == "*also bold*"


def test_padded_bold_is_not_turned_into_a_bullet() -> None:
    """Models often emit ``** I found: **``; Slack ``* text`` is a list item."""
    assert convert("** I found: ** the disk is full") == "*I found:* the disk is full"
    assert convert("** Want me to: ** rerun the job") == "*Want me to:* rerun the job"


def test_dunder_filenames_are_not_eaten_as_bold() -> None:
    assert convert("see __init__.py") == "see __init__.py"
    assert convert("split plugins/action/__init__.py") == "split plugins/action/__init__.py"


def test_heading_with_inner_bold_is_not_double_wrapped() -> None:
    assert convert("# **Title**") == "*Title*"
    assert convert("## Root cause") == "*Root cause*"


def test_headings_become_bold_lines() -> None:
    assert convert("# Title") == "*Title*"
    assert convert("### Recommended Actions") == "*Recommended Actions*"


def test_bullets_become_slack_dots() -> None:
    assert convert("- one\n- two") == "• one\n• two"
    assert convert("  * nested") == "  • nested"


def test_links_become_slack_link_syntax() -> None:
    assert convert("see [docs](https://x.com/y)") == "see <https://x.com/y|docs>"


def test_code_spans_are_preserved_verbatim() -> None:
    assert convert("run `kubectl **get** pods`") == "run `kubectl **get** pods`"
    block = "```\n**not bold**\n```"
    assert convert(block) == block


def test_empty_and_plain_text_unchanged() -> None:
    assert convert("") == ""
    assert convert("just a sentence.") == "just a sentence."
