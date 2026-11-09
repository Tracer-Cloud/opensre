"""Tests for shared Markdown emphasis tightening."""

from __future__ import annotations

from infrastructure.text.markdown import tighten_markdown_emphasis as tighten


def test_strips_inner_spaces_around_bold() -> None:
    assert tighten("** I found: ** ok") == "**I found:** ok"
    assert tighten("**Want me to:** next") == "**Want me to:** next"


def test_leaves_code_spans_and_fences_alone() -> None:
    assert tighten("run `kubectl ** get ** pods`") == "run `kubectl ** get ** pods`"
    block = "```\n** not bold **\n```"
    assert tighten(block) == block


def test_empty_and_plain_text_unchanged() -> None:
    assert tighten("") == ""
    assert tighten("just a sentence.") == "just a sentence."


def test_drops_empty_padded_markers() -> None:
    assert tighten("before **  ** after") == "before  after"
