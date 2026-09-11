"""Read-only shell commands run without approval; mutating ones still ask."""

from __future__ import annotations

import pytest

from config.constants.repl_autonomy import AutoLevel
from tools.interactive_shell.shared import apply_auto_level, apply_plan_only_gate
from tools.interactive_shell.shell import read_only as shell_read_only
from tools.interactive_shell.shell.policy import evaluate_shell_command
from tools.interactive_shell.shell.read_only import is_read_only_shell_command

_READ_ONLY = [
    'find /Users/x -maxdepth 4 -iname "*opensre*" 2>/dev/null | head -50',
    'ls -la /Users/x 2>/dev/null | head -60; echo "---"; ls /tmp',
    "git status",
    "git log --oneline | head",
    "cat file | grep needle | wc -l",
    "sort file | uniq",
    'grep -E "(foo|bar)" file | head',  # quoted regex parens are safe
    'cd /Users/x/repo && git status --short --branch | head -5; echo "==="; grep -Ei foo Makefile',
    "cd /tmp && ls",
    "git remote -v",
    "git branch -a",
    "cd /x && git status | head; git remote -v; git branch -a",
    "git symbolic-ref HEAD",
    "git symbolic-ref --short HEAD",
    "git reflog",
    "git reflog show",
    "git reflog list",
    "git reflog exists HEAD",
    "git reflog HEAD",
    "rg needle file",
    "rg --pretty needle",  # --pretty is display, not the --pre preprocessor
    "rg --pre-glob '*.pdf' needle",  # glob-only; no preprocessor without --pre
    "git diff --no-ext-diff --no-textconv",  # helpers explicitly disabled
    "git show --no-ext-diff --no-textconv HEAD",
    "git log -p --no-ext-diff --no-textconv",
    "git blame --no-ext-diff --no-textconv file",
]

_MUTATING = [
    "rm -rf /tmp/x",
    "ls > out.txt",
    "cat a | tee b",
    "git commit -m x",
    "find . -delete",
    "find . -exec rm {} ;",
    "sort -o out.txt file",
    "sudo ls",
    "ls | xargs rm",
    "yq -i .x f.yaml",
    "sed -i s/a/b/ f",
    "python script.py",
    "cat $(rm x)",
    "cd /tmp && rm -rf x",
    "git remote add origin url",
    "git branch -d feature",
    "git branch --set-upstream-to=origin/main",
    "git branch -u origin/main",
    "git diff --output=/tmp/patch.diff",
    "git diff -o/tmp/patch.diff",
    "git log --output=/tmp/history.txt",
    "git show --output=/tmp/commit.patch HEAD",
    "git show -o/tmp/commit.patch HEAD",
    "git config user.name Bob",
    "git symbolic-ref HEAD refs/heads/foo",
    "git symbolic-ref --delete HEAD",
    "git symbolic-ref -d HEAD",
    "git symbolic-ref -mreason HEAD refs/heads/foo",
    "git reflog expire --all",
    "git reflog delete HEAD@{1}",
    "git reflog drop --all",
    "git reflog write HEAD old new msg",
    "git --exec-path=/tmp/evil status",
    "git --exec-path /tmp/evil status",
    "ls -la\nrm -rf /tmp/x",  # newline separates a mutation — must gate
    'date -s "2020-01-01"',  # sets system clock
    "date -s2026-08-27",  # attached short-option value also sets the clock
    "date 12121212",  # legacy positional MMDDhhmm form sets the clock
    "date 010203042025.00",
    "hostname newname",  # sets kernel hostname
    "hostname -F/tmp/x",  # Linux attached -F writes hostname from a file
    "hostname -F /tmp/x",
    "LD_PRELOAD=/evil.so ls",  # env prefix can inject code
    "git ls-remote ext::evil",  # ext:: transport runs a helper
    "git ls-remote EXT::evil",  # protocol match is case-insensitive
    "git ls-remote --upload-pack=/tmp/evil origin",  # helper-path override
    "git ls-remote --upload-pack /tmp/evil origin",
    "git -c protocol.ext.allow=always ls-remote origin",  # process-local config
    "git -cprotocol.ext.allow=always ls-remote origin",
    "git diff --ext-diff",  # runs diff.external
    "git log --ext-diff",
    "git show --textconv HEAD",
    "git diff --textconv",
    "git diff",  # default-on textconv / gitattributes diff drivers
    "git diff --no-ext-diff",  # textconv still default-on without --no-textconv
    "git diff --no-textconv",  # external diff drivers still default-on
    "git show HEAD",
    "git log -p",
    "git whatchanged",
    "git blame file",
    "rg --pre python pattern",  # preprocessor runs per matched file
    "rg --pre=python pattern",
    "rg --pre=/tmp/evil.sh pattern",
    "rg --hostname-bin=/tmp/evil pattern",
    "sort --compress-program=/tmp/evil file",
    "ls --pre=/tmp/evil",  # allowlisted names still fail closed on helper-exec flags
    "./ls",  # path-qualified executable is not the allowlisted command
    "/tmp/git status",  # attacker-controlled path
    "diff <(rm /tmp/x) file",  # process substitution runs a nested command
    "(rm /tmp/x) && true",  # bare subshell
    'echo "$(rm /tmp/x)"',  # $() expands inside double quotes
]


@pytest.mark.parametrize("command", _READ_ONLY)
def test_read_only_commands_are_classified_read_only(command: str) -> None:
    assert is_read_only_shell_command(command) is True


@pytest.mark.parametrize("command", _MUTATING)
def test_mutating_commands_are_not_read_only(command: str) -> None:
    assert is_read_only_shell_command(command) is False


def test_read_only_shell_runs_without_approval_at_every_level_and_under_plan_only() -> None:
    result = evaluate_shell_command("find . -iname x | head")
    assert result.shell_classification == "read_only"
    # Neither the /auto gate nor the plan-only gate promotes it to ask.
    assert apply_auto_level(result, AutoLevel.MED).verdict == "allow"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "allow"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "allow"


def test_mutating_shell_still_asks_when_gated() -> None:
    result = evaluate_shell_command("rm -rf /tmp/x")
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.MED).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"


def test_unquoted_glob_bypasses_neither_auto_nor_plan_only_gates() -> None:
    result = evaluate_shell_command("sort *")

    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"


@pytest.mark.parametrize(
    ("command", "classification"),
    [("date", "unrestricted"), ("date 09-12-2026", "unrestricted"), ("date /t", "read_only")],
)
def test_windows_date_only_auto_runs_the_display_form(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    classification: str,
) -> None:
    monkeypatch.setattr(shell_read_only.os, "name", "nt")

    result = evaluate_shell_command(command)

    assert result.shell_classification == classification
    expected_verdict = "allow" if classification == "read_only" else "ask"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == expected_verdict
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == expected_verdict


@pytest.mark.parametrize(
    "command",
    [
        "git diff --output=/tmp/patch.diff",
        "git log --output=/tmp/history.txt",
        "git log --output /tmp/history.txt",
        "git show --output=/tmp/commit.patch HEAD",
        "git show -o/tmp/commit.patch HEAD",
    ],
)
def test_git_output_write_bypasses_neither_auto_nor_plan_only_gates(command: str) -> None:
    """``--output`` / ``-o`` write a file for diff-family porcelain, not only ``diff``.

    Those commands must stay ``unrestricted`` so low-auto and plan-only still ask.
    """
    result = evaluate_shell_command(command)
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "date 12121212",
        "hostname -F/tmp/x",
        "git ls-remote --upload-pack=/tmp/evil origin",
        "git -cprotocol.ext.allow=always ls-remote origin",
        "git ls-remote ext::evil",
        "git diff --ext-diff",
        "git show --textconv HEAD",
        "git diff",
        "git show HEAD",
        "git log -p",
    ],
)
def test_mutation_and_helper_forms_bypass_neither_auto_nor_plan_only_gates(
    command: str,
) -> None:
    """Clock/hostname setters and git helper/config injectors must still ask."""
    result = evaluate_shell_command(command)
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "rg --pre python pattern",
        "rg --pre=python pattern",
        "rg --pre=/tmp/evil.sh pattern",
        "rg --hostname-bin=/tmp/evil pattern",
        "sort --compress-program=/tmp/evil file",
        "ls --pre=/tmp/evil",
    ],
)
def test_rg_helper_forms_bypass_neither_auto_nor_plan_only_gates(command: str) -> None:
    """``rg --pre`` / ``--hostname-bin`` run a helper; low-auto and plan-only must still ask."""
    result = evaluate_shell_command(command)
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "git symbolic-ref HEAD refs/heads/foo",
        "git symbolic-ref --delete HEAD",
        "git reflog expire --all",
        "git reflog delete HEAD@{1}",
        "git reflog drop --all",
        "git reflog write HEAD old new msg",
        "git --exec-path=/tmp/evil status",
    ],
)
def test_git_ref_mutations_bypass_neither_auto_nor_plan_only_gates(command: str) -> None:
    """``symbolic-ref`` set/delete, ``reflog`` write verbs, and ``--exec-path`` mutate.

    They must stay ``unrestricted`` so low-auto and plan-only still ask.
    """
    result = evaluate_shell_command(command)
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"


@pytest.mark.parametrize(
    "command",
    [
        "git diff --ext-diff",
        "git diff --textconv",
        "git diff",
        "git show HEAD",
        "git log -p",
        "git blame file",
    ],
)
def test_git_diff_helpers_bypass_neither_auto_nor_plan_only_gates(command: str) -> None:
    """Diff-family porcelain runs configured helpers unless both drivers are opted out."""
    result = evaluate_shell_command(command)
    assert result.shell_classification == "unrestricted"
    assert apply_auto_level(result, AutoLevel.LOW).verdict == "ask"
    assert apply_plan_only_gate(result, plan_only_active=True).verdict == "ask"
