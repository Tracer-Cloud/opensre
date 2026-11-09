"""Regression: two commands must never claim the same slash name.

``SLASH_COMMANDS`` is built as ``{cmd.name: cmd for cmd in ...}``, so a
duplicate name silently discards whichever module merges first — the command
still exists, still passes the CLI-parity check, and simply does the wrong
thing. Nothing else in the suite catches that, because both the name and the
parity pairing survive the collapse.
"""

from __future__ import annotations

from collections import Counter

from surfaces.interactive_shell.command_registry.catalog import (
    _MERGED_SEQUENCE,
    SLASH_COMMANDS,
)


def test_no_two_commands_claim_the_same_name() -> None:
    # Arrange
    counts = Counter(command.name for command in _MERGED_SEQUENCE)

    # Act
    duplicates = {name for name, count in counts.items() if count > 1}

    # Assert
    assert sorted(duplicates) == [], (
        "these slash names are registered more than once and would overwrite "
        f"each other: {sorted(duplicates)}"
    )


def test_every_registered_command_survives_the_lookup_table() -> None:
    """No command is lost between the merged sequence and the dispatch dict."""
    # Arrange / Act
    lost = sorted({command.name for command in _MERGED_SEQUENCE} - set(SLASH_COMMANDS))

    # Assert
    assert lost == []
    assert len(SLASH_COMMANDS) == len(_MERGED_SEQUENCE)
