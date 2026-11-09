"""Tests for shared CLI output validation."""

from __future__ import annotations

import pytest

from integrations.llm_cli.output import require_nonempty_output


def _failure_detail(*, stdout: str, stderr: str, returncode: int) -> str:
    return f"exit {returncode}: {stderr or stdout}"


def test_require_nonempty_output_preserves_adapter_failure_detail() -> None:
    with pytest.raises(RuntimeError, match=r"exit 1: unavailable \(empty output\)"):
        require_nonempty_output("", "unavailable", 1, _failure_detail)
