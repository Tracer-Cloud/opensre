"""Deterministic local fixers for GitHub Code Quality findings."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from integrations.coding_agent import CodingResult
from integrations.git import capture_worktree_changes
from integrations.github.tools.security_fix.context import SecurityAlertContext
from integrations.github.tools.security_fix.local_rules import local_ruff_codes

_MAX_DIFF_CHARS = 24_000
_RUFF_TIMEOUT_SEC = 60


def try_builtin_local_fix(
    ctx: SecurityAlertContext, workspace: str
) -> tuple[CodingResult | None, str]:
    """Try a deterministic local fix; return ``None`` when no builtin applies."""
    if ctx.alert_type not in {"code_quality", "code_scanning"}:
        return None, f"No built-in local fixer handles {ctx.alert_type} findings."

    codes = local_ruff_codes(rule_id=ctx.rule_id, title=ctx.summary)
    if not codes:
        return (
            None,
            f"No built-in local fixer supports {ctx.alert_type} rule {ctx.rule_id or ctx.summary}.",
        )

    target = _target_file(workspace, ctx.location_path)
    if target is None:
        return _failure("The Code Quality finding did not include a safe local file path."), ""

    if not target.is_file():
        return _failure(f"Finding file does not exist in the workspace: {ctx.location_path}"), ""

    # Only fall back to the finding's original line number when Ruff did not
    # rewrite the file. After Ruff removes an F401 import, later imports shift
    # up — applying the stale line would delete an unrelated import.
    before = target.read_text(encoding="utf-8")
    _run_ruff_fix(workspace, target, codes)
    after = target.read_text(encoding="utf-8")
    if "F401" in codes and after == before:
        _remove_single_import_line(target, ctx.start_line)

    changes = capture_worktree_changes(workspace, max_diff_chars=_MAX_DIFF_CHARS)
    if ctx.location_path not in changes.changed_files:
        return (
            _failure("The built-in local fixer ran, but it did not produce a repository change."),
            "",
        )

    summary = (
        f"Applied built-in Code Quality fixer for {ctx.summary or ctx.rule_id} "
        f"in {ctx.location_path}."
    )
    return (
        CodingResult(
            success=True,
            summary=summary,
            changed_files=changes.changed_files,
            diff=changes.diff,
            diff_truncated=changes.diff_truncated,
        ),
        "",
    )


def _target_file(workspace: str, relative_path: str) -> Path | None:
    if not relative_path.strip():
        return None
    root = Path(workspace).resolve()
    target = (root / relative_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _run_ruff_fix(workspace: str, target: Path, codes: tuple[str, ...]) -> None:
    argv = _ruff_argv()
    if argv is None:
        return
    subprocess.run(
        [
            *argv,
            "check",
            "--select",
            ",".join(codes),
            "--fix",
            "--exit-zero",
            str(target),
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=_RUFF_TIMEOUT_SEC,
        check=False,
    )


def _ruff_argv() -> list[str] | None:
    binary = shutil.which("ruff")
    if binary:
        return [binary]
    return [sys.executable, "-m", "ruff"]


def _remove_single_import_line(target: Path, start_line: int | None) -> None:
    if start_line is None or start_line < 1:
        return
    lines = target.read_text(encoding="utf-8").splitlines(keepends=True)
    index = start_line - 1
    if index >= len(lines):
        return
    stripped = lines[index].strip()
    is_single_import = (
        stripped.startswith("import ")
        and "," not in stripped
        and ";" not in stripped
        and not stripped.endswith("\\")
    )
    is_single_from_import = (
        stripped.startswith("from ")
        and " import " in stripped
        and "," not in stripped
        and "(" not in stripped
        and ")" not in stripped
        and ";" not in stripped
        and not stripped.endswith("\\")
    )
    if not (is_single_import or is_single_from_import):
        return
    del lines[index]
    target.write_text("".join(lines), encoding="utf-8")


def _failure(message: str) -> CodingResult:
    return CodingResult(success=False, summary="", error=message, returncode=-1)


__all__ = ["try_builtin_local_fix"]
