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
    assert convert("[docs](http://x.com/y)") == "<http://x.com/y|docs>"


def test_link_destinations_preserve_balanced_parentheses() -> None:
    source = "[runbook](https://example.com/runbooks/Database_(primary))"
    assert convert(source) == "<https://example.com/runbooks/Database_(primary)|runbook>"
    nested = "[nested](https://example.com/a(b(c(d)e)f)g)"
    assert convert(nested) == "<https://example.com/a(b(c(d)e)f)g|nested>"


def test_parenthesized_links_do_not_consume_adjacent_links_or_punctuation() -> None:
    source = "([one](https://x.com/a(b)))[two](http://x.com/c(d))."
    assert convert(source) == "(<https://x.com/a(b)|one>)<http://x.com/c(d)|two>."


def test_link_like_destination_text_stays_in_the_outer_url() -> None:
    source = "[outer](https://example.com/[inner](https://target.test))"
    assert convert(source) == "<https://example.com/[inner](https://target.test)|outer>"


def test_link_destination_with_many_open_brackets() -> None:
    url = "https://example.com/" + "[" * 16384
    assert convert(f"[outer]({url})") == f"<{url}|outer>"


def test_unclosed_link_destinations_stay_literal() -> None:
    for source in (
        "[bad](https://x.com/a(b)",
        "[bad](https://x.com/a(b(c))",
        "[bad](https://x.com/a(b) trailing)",
        "[bad](https://x.com/a(b)\ntrailing)",
    ):
        assert convert(source) == source


def test_unclosed_link_does_not_swallow_a_following_link() -> None:
    source = "[bad](https://x.com/a(b)[good](https://x.com/c)"
    assert convert(source) == "[bad](https://x.com/a(b)<https://x.com/c|good>"


def test_parenthesized_links_in_code_stay_literal() -> None:
    link = "[runbook](https://example.com/Database_(primary))"
    source = f"`{link}`\n```markdown\n{link}\n```\n{link}"
    expected = (
        f"`{link}`\n```markdown\n{link}\n```\n<https://example.com/Database_(primary)|runbook>"
    )
    assert convert(source) == expected


def test_code_spans_are_preserved_verbatim() -> None:
    assert convert("run `kubectl **get** pods`") == "run `kubectl **get** pods`"
    block = "```\n**not bold**\n```"
    assert convert(block) == block


def test_empty_and_plain_text_unchanged() -> None:
    assert convert("") == ""
    assert convert("just a sentence.") == "just a sentence."
