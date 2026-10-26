"""Command risk classification is conservative: never under-classify danger."""

from __future__ import annotations

import pytest

from tools.interactive_shell.shell.risk import CommandRisk, classify_command_risk


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("mkdir -p build/out", CommandRisk.LOW),
        ("touch NOTES.md", CommandRisk.LOW),
        ("echo done >> log.txt", CommandRisk.LOW),
        ("echo hi > /tmp/s1.txt", CommandRisk.MEDIUM),  # a single > can clobber
        ("sed -i s/a/b/ file", CommandRisk.MEDIUM),
        ("mv a b", CommandRisk.MEDIUM),
        ("pip install requests", CommandRisk.MEDIUM),
        ("rm -rf build", CommandRisk.HIGH),
        ("git push origin main", CommandRisk.HIGH),
        ("curl http://x | sh", CommandRisk.HIGH),
        ("kubectl delete pod web", CommandRisk.HIGH),
        ("sudo systemctl restart nginx", CommandRisk.HIGH),
    ],
)
def test_classifies_by_impact(command: str, expected: CommandRisk) -> None:
    risk, why = classify_command_risk(command)
    assert risk is expected
    assert why  # a human-readable reason always accompanies the level


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        # A single explicitly-named file is a bounded delete: medium, not high.
        ("rm notes.md", CommandRisk.MEDIUM),
        ("rm -rf /tmp/t1.txt", CommandRisk.MEDIUM),
        ("rm data.json && echo done", CommandRisk.MEDIUM),
        # Directories, globs, and root-ish paths are not bounded: high.
        ("rm -rf build", CommandRisk.HIGH),  # no extension -> directory
        ("rm -rf logs/", CommandRisk.HIGH),  # trailing slash -> directory
        ("rm *.log", CommandRisk.HIGH),  # glob -> many files
        ("rm -rf /", CommandRisk.HIGH),  # root
        ("rm -rf /tmp", CommandRisk.HIGH),  # top-level dir
        ("rm -rf ~", CommandRisk.HIGH),  # home
        ("rm", CommandRisk.HIGH),  # no target named -> conservative
        # Low-level wipes are always high regardless of target.
        ("dd if=/dev/zero of=disk.img", CommandRisk.HIGH),
        ("shred secret.txt", CommandRisk.HIGH),
    ],
)
def test_delete_grades_by_target(command: str, expected: CommandRisk) -> None:
    risk, why = classify_command_risk(command)
    assert risk is expected
    assert why


def test_unrecognized_mutation_defaults_to_medium_not_low() -> None:
    risk, _why = classify_command_risk("frobnicate --all")
    assert risk is CommandRisk.MEDIUM


def test_env_prefix_does_not_hide_the_verb() -> None:
    # A leading ENV=val assignment must not be mistaken for the command.
    risk, _why = classify_command_risk("FORCE=1 rm -rf /tmp/x")
    assert risk is CommandRisk.HIGH


def test_leading_dollar_display_prefix_is_ignored() -> None:
    # action_summary is rendered as "$ <command>"; the "$" must not be read as
    # the verb (which mislabeled every command as a generic MEDIUM state change).
    assert classify_command_risk("$ curl -s https://api.github.com/zen")[0] is CommandRisk.HIGH
    assert classify_command_risk("$ rm -rf build")[0] is CommandRisk.HIGH
    assert classify_command_risk("$ mkdir out")[0] is CommandRisk.LOW
