"""Opensre CLI execution-plan policy for read-only vs background commands."""

from __future__ import annotations

from pathlib import Path

import pytest

import tools.interactive_shell.cli as opensre_cli
from tools.interactive_shell.cli import (
    OpensreCommandClass,
    OpensreExecutionMode,
    build_opensre_cli_argv,
    build_opensre_execution_plan,
    classify_opensre_command,
)


def test_cli_argv_absolutizes_relative_reused_entrypoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(opensre_cli.sys, "argv", ["./.venv/bin/opensre"])

    argv = build_opensre_cli_argv(["health"])

    assert argv == [str((tmp_path / ".venv/bin/opensre").resolve()), "health"]


def test_cli_argv_preserves_bare_entrypoint_for_path_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(opensre_cli.sys, "argv", ["opensre"])

    assert build_opensre_cli_argv(["health"]) == ["opensre", "health"]


@pytest.mark.parametrize(
    ("tokens", "classification", "mode"),
    [
        (["health"], OpensreCommandClass.READ_ONLY, OpensreExecutionMode.FOREGROUND),
        (["version"], OpensreCommandClass.READ_ONLY, OpensreExecutionMode.FOREGROUND),
        (
            ["integrations", "verify", "grafana"],
            OpensreCommandClass.READ_ONLY,
            OpensreExecutionMode.FOREGROUND,
        ),
        (
            ["integrations", "list"],
            OpensreCommandClass.READ_ONLY,
            OpensreExecutionMode.FOREGROUND,
        ),
        (
            ["integrations", "show", "grafana"],
            OpensreCommandClass.READ_ONLY,
            OpensreExecutionMode.FOREGROUND,
        ),
        (
            ["integrations", "status"],
            OpensreCommandClass.READ_ONLY,
            OpensreExecutionMode.FOREGROUND,
        ),
        # Bare ``integrations`` defaults to list (read-only foreground).
        (["integrations"], OpensreCommandClass.READ_ONLY, OpensreExecutionMode.FOREGROUND),
        (
            ["integrations", "setup", "grafana"],
            OpensreCommandClass.MUTATING,
            OpensreExecutionMode.BACKGROUND,
        ),
    ],
)
def test_build_opensre_execution_plan_modes(
    tokens: list[str],
    classification: OpensreCommandClass,
    mode: OpensreExecutionMode,
) -> None:
    plan = build_opensre_execution_plan(tokens)
    assert plan.classification is classification
    assert plan.execution_mode is mode
    assert classify_opensre_command(tokens) == classification.value
    if classification is OpensreCommandClass.MUTATING:
        assert plan.requires_confirmation is True
    else:
        assert plan.requires_confirmation is False


def test_integrations_verify_is_not_backgrounded() -> None:
    """Regression: background verify left Invoking tools + 1 task running idle."""
    plan = build_opensre_execution_plan(["integrations", "verify", "grafana"])
    assert plan.execution_mode is OpensreExecutionMode.FOREGROUND
    assert plan.classification is OpensreCommandClass.READ_ONLY
