"""strip_html must remove HTML tags without deleting Markdown report text."""

from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.delivery import strip_html

_REPORT = (
    "## Checkout latency\n"
    "- p99 latency < 200ms target breached\n"
    "- Error rate 7% (threshold > 5%)\n"
    "- Runbook: <https://runbooks.example.com/checkout>"
)


def test_strip_html_keeps_comparison_operators_and_autolinks() -> None:
    assert strip_html(_REPORT) == _REPORT


@pytest.mark.parametrize(
    "text",
    [
        "ssh <host> and restart <pod-name>",
        "if a<b and c>d",
        "retry when count <3 or >10",
    ],
)
def test_strip_html_keeps_non_tag_angle_brackets(text: str) -> None:
    assert strip_html(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("<b>bold</b> and <i>it</i>", "bold and it"),
        ('<a href="https://x">link</a><br/>', "link"),
        ("<code>kubectl</code> <PRE>x</PRE>", "kubectl x"),
        ('<span class="tg-spoiler">s</span>', "s"),
    ],
)
def test_strip_html_removes_html_tags(text: str, expected: str) -> None:
    assert strip_html(text) == expected
