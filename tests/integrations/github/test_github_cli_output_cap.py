"""``github_cli`` bounds its own output instead of letting the context budget cut it.

A raw ``gh api`` list endpoint answers with the whole object graph — 30 workflow
runs is roughly 382k characters. Unbounded, that reached the message and the
context budget trimmed the tail silently, so the agent read a couple of records
and reported a 0% failure rate computed from one run. Capping here keeps the cut
visible: the payload says ``truncated`` so the reply can admit what it missed.
"""

from __future__ import annotations

from core.tool.output import tool_output_byte_budget
from integrations.github.tools.github_cli.runner import MAX_GH_OUTPUT_CHARS, _cap_output


def test_output_within_the_cap_is_untouched_and_unflagged() -> None:
    # Arrange
    text = "x" * (MAX_GH_OUTPUT_CHARS - 1)

    # Act
    capped, truncated = _cap_output(text)

    # Assert
    assert capped == text
    assert truncated is False


def test_an_oversized_payload_is_cut_and_flagged() -> None:
    # Arrange: the shape that produced the one-run answer.
    text = "y" * (MAX_GH_OUTPUT_CHARS * 16)

    # Act
    capped, truncated = _cap_output(text)

    # Assert: the agent can see it did not get everything.
    assert len(capped) == MAX_GH_OUTPUT_CHARS
    assert truncated is True


def test_exactly_the_cap_is_not_a_truncation() -> None:
    capped, truncated = _cap_output("z" * MAX_GH_OUTPUT_CHARS)
    assert len(capped) == MAX_GH_OUTPUT_CHARS
    assert truncated is False


def test_a_whole_source_file_fits_under_the_cap_and_the_cap_under_the_shared_budget() -> None:
    """A 6,000-character cap made the agent page one file across several model calls."""
    # Arrange: a 20,000-character file read, e.g. ``gh api .../contents/...``.
    source_file = "line of code\n" * (20_000 // 13)

    # Act
    capped, truncated = _cap_output(source_file)

    # Assert: returned whole, and the envelope still fits the shared tool-output budget.
    assert capped == source_file
    assert truncated is False
    assert tool_output_byte_budget() > MAX_GH_OUTPUT_CHARS
