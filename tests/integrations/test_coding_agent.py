"""Tests for the agent-neutral coding-agent seam (auto selection + backends)."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from integrations.coding_agent import (
    CodingResult,
    claude_code_backend,
    codex_backend,
    coding_agent_provider,
    cursor_backend,
    run_coding_task,
    verify_coding_agent,
)
from integrations.coding_agent.runner import _BACKENDS

_OK = CodingResult(success=True, summary="done")


def _fake_backends(
    *,
    pi: tuple[bool, str] = (False, "pi not installed"),
    claude: tuple[bool, str] = (False, "claude not installed"),
    codex: tuple[bool, str] = (False, "codex not installed"),
    cursor: tuple[bool, str] = (False, "cursor not installed"),
) -> dict[str, tuple[MagicMock, MagicMock]]:
    """Backend table stand-ins keyed like ``_BACKENDS`` (run mock, verify mock)."""
    return {
        "pi": (MagicMock(return_value=_OK), MagicMock(return_value=pi)),
        "claude-code": (MagicMock(return_value=_OK), MagicMock(return_value=claude)),
        "codex": (MagicMock(return_value=_OK), MagicMock(return_value=codex)),
        "cursor": (MagicMock(return_value=_OK), MagicMock(return_value=cursor)),
    }


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_default_provider_is_auto() -> None:
    assert coding_agent_provider({}) == "auto"
    assert coding_agent_provider({"CODING_AGENT": " Claude "}) == "claude"


# --------------------------------------------------------------------------- #
# auto selection
# --------------------------------------------------------------------------- #
def test_auto_verify_picks_first_ready_backend() -> None:
    table = _fake_backends(claude=(True, "claude ready"), codex=(True, "codex ready"))
    with patch.dict(_BACKENDS, table):
        available, detail = verify_coding_agent("auto")
    assert available is True
    assert detail == "claude-code: claude ready"
    # Selection stops at the first ready backend; codex is never probed.
    table["codex"][1].assert_not_called()
    table["cursor"][1].assert_not_called()


def test_auto_verify_reports_every_backend_when_none_ready() -> None:
    with patch.dict(_BACKENDS, _fake_backends()):
        available, detail = verify_coding_agent("auto")
    assert available is False
    assert "pi: pi not installed" in detail
    assert "claude-code: claude not installed" in detail
    assert "codex: codex not installed" in detail
    assert "cursor: cursor not installed" in detail


def test_auto_run_dispatches_to_first_ready_backend() -> None:
    table = _fake_backends(claude=(True, "claude ready"))
    with patch.dict(_BACKENDS, table):
        result = run_coding_task("fix", workspace="/w", model=None, timeout_sec=60, provider="auto")
    assert result.success is True
    table["claude-code"][0].assert_called_once_with(
        "fix", workspace="/w", model=None, timeout_sec=60, on_progress=None
    )
    table["pi"][0].assert_not_called()


def test_auto_run_with_no_ready_backend_returns_error() -> None:
    with patch.dict(_BACKENDS, _fake_backends()):
        result = run_coding_task("fix", workspace="/w", model=None, timeout_sec=60, provider="auto")
    assert result.success is False
    assert "No coding agent is ready" in (result.error or "")


def test_alias_claude_resolves_to_claude_code() -> None:
    table = _fake_backends(claude=(True, "ready"))
    with patch.dict(_BACKENDS, table):
        available, _ = verify_coding_agent("claude")
    assert available is True
    table["claude-code"][1].assert_called_once()


def test_unsupported_provider_is_reported() -> None:
    available, detail = verify_coding_agent("gemini")
    assert available is False
    assert "Unsupported coding agent" in detail
    result = run_coding_task("fix", workspace="/w", model=None, timeout_sec=60, provider="gemini")
    assert result.success is False


# --------------------------------------------------------------------------- #
# backends — argv construction and pre-flight failures. The agent process is
# faked at agent_exec's Popen; git calls are faked at integrations.git.local's
# subprocess.run (patching either name patches the global subprocess module, so
# real git cannot be mixed with a faked agent process in one test).
# --------------------------------------------------------------------------- #
_POPEN = "integrations.llm_cli.agent_exec.subprocess.Popen"
_GIT_RUN = "integrations.git.local.subprocess.run"


def _git_run_side_effect(cmd: list[str], **_: object) -> MagicMock:
    sub = cmd[1]  # only git calls reach subprocess.run
    if sub == "rev-parse":
        return MagicMock(returncode=0, stdout="true\n", stderr="")
    if sub == "status":
        if "-z" in cmd:
            return MagicMock(returncode=0, stdout=" M foo.py\0", stderr="")
        return MagicMock(returncode=0, stdout=" M foo.py\n", stderr="")
    if sub == "diff":
        return MagicMock(returncode=0, stdout="diff --git a/foo.py b/foo.py\n", stderr="")
    return MagicMock(returncode=0, stdout="", stderr="")


class _FakePopen:
    def __init__(self, *, stdout: str = "done\n", returncode: int = 0) -> None:
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(stdout)
        self.stderr = io.StringIO("")
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self.returncode


@patch(_POPEN)
@patch(_GIT_RUN, side_effect=_git_run_side_effect)
@patch("integrations.coding_agent.claude_code_backend._resolve_binary")
def test_claude_code_backend_builds_agentic_argv(
    mock_resolve: MagicMock, _mock_git: MagicMock, mock_popen: MagicMock, tmp_path: Path
) -> None:
    mock_resolve.return_value = "/usr/bin/claude"
    mock_popen.return_value = _FakePopen()

    result = claude_code_backend.run(
        "fix the bug", workspace=str(tmp_path), model="claude-opus-5", timeout_sec=60
    )
    assert result.success is True
    argv = mock_popen.call_args.args[0]
    assert argv[0] == "/usr/bin/claude"
    assert "-p" in argv
    assert "--permission-mode" in argv
    assert "acceptEdits" in argv
    assert "--allowedTools" in argv
    assert "--model" in argv
    assert "claude-opus-5" in argv
    # The guarded prompt forbids commits/pushes.
    prompt = argv[argv.index("-p") + 1]
    assert "Do NOT create a git commit or push changes" in prompt


@patch("integrations.coding_agent.claude_code_backend._resolve_binary", return_value=None)
def test_claude_code_backend_binary_missing(_mock_resolve: MagicMock, tmp_path: Path) -> None:
    result = claude_code_backend.run("x", workspace=str(tmp_path), model=None, timeout_sec=60)
    assert result.success is False
    assert "Claude Code CLI not found" in (result.error or "")


@patch("integrations.coding_agent.claude_code_backend._resolve_binary")
def test_claude_code_backend_non_git_workspace_fails(
    mock_resolve: MagicMock, tmp_path: Path
) -> None:
    mock_resolve.return_value = "/usr/bin/claude"
    result = claude_code_backend.run("x", workspace=str(tmp_path), model=None, timeout_sec=60)
    assert result.success is False
    assert "not a git repository" in (result.error or "")


@patch(_POPEN)
@patch(_GIT_RUN, side_effect=_git_run_side_effect)
@patch("integrations.coding_agent.codex_backend._resolve_binary")
def test_codex_backend_builds_workspace_write_argv(
    mock_resolve: MagicMock, _mock_git: MagicMock, mock_popen: MagicMock, tmp_path: Path
) -> None:
    mock_resolve.return_value = "/usr/bin/codex"
    mock_popen.return_value = _FakePopen()

    result = codex_backend.run(
        "fix the bug", workspace=str(tmp_path), model="gpt-5.1-codex", timeout_sec=60
    )
    assert result.success is True
    argv = mock_popen.call_args.args[0]
    assert argv[0] == "/usr/bin/codex"
    assert "exec" in argv
    assert "workspace-write" in argv
    assert "-C" in argv
    assert "-m" in argv
    assert "gpt-5.1-codex" in argv
    # The guarded prompt is the trailing positional argument.
    assert "Do NOT create a git commit or push changes" in argv[-1]


@patch("integrations.coding_agent.codex_backend._resolve_binary", return_value=None)
def test_codex_backend_binary_missing(_mock_resolve: MagicMock, tmp_path: Path) -> None:
    result = codex_backend.run("x", workspace=str(tmp_path), model=None, timeout_sec=60)
    assert result.success is False
    assert "Codex CLI not found" in (result.error or "")


@patch(_POPEN)
@patch(_GIT_RUN, side_effect=_git_run_side_effect)
@patch("integrations.llm_cli.cursor.CursorAdapter._resolve_binary")
def test_cursor_backend_builds_agentic_argv(
    mock_resolve: MagicMock, _mock_git: MagicMock, mock_popen: MagicMock, tmp_path: Path
) -> None:
    mock_resolve.return_value = "/usr/bin/agent"
    mock_popen.return_value = _FakePopen()

    result = cursor_backend.run(
        "fix the bug", workspace=str(tmp_path), model="auto", timeout_sec=60
    )
    assert result.success is True
    argv = mock_popen.call_args.args[0]
    assert argv[0] == "/usr/bin/agent"
    assert "--print" in argv
    assert "--trust" in argv
    assert "--workspace" in argv
    assert str(tmp_path) in argv
    assert "--model" in argv
    assert "auto" in argv
    assert mock_popen.call_args.kwargs["stdin"] is subprocess.PIPE


@patch(_GIT_RUN, side_effect=_git_run_side_effect)
@patch("integrations.llm_cli.cursor.CursorAdapter._resolve_binary", return_value=None)
def test_cursor_backend_binary_missing(
    _mock_resolve: MagicMock, _mock_git: MagicMock, tmp_path: Path
) -> None:
    result = cursor_backend.run("x", workspace=str(tmp_path), model=None, timeout_sec=60)
    assert result.success is False
    assert "Cursor Agent CLI not found" in (result.error or "")


# --------------------------------------------------------------------------- #
# backend verify — mirrors the Pi verifier semantics
# --------------------------------------------------------------------------- #
@patch("integrations.coding_agent.claude_code_backend.ClaudeCodeAdapter")
def test_claude_code_verify_unclear_auth_counts_as_available(mock_cls: MagicMock) -> None:
    mock_cls.return_value.detect.return_value = MagicMock(
        installed=True, logged_in=None, detail="auth unclear"
    )
    available, _ = claude_code_backend.verify()
    assert available is True


@patch("integrations.coding_agent.codex_backend.CodexAdapter")
def test_codex_verify_not_authed_is_unavailable(mock_cls: MagicMock) -> None:
    mock_cls.return_value.detect.return_value = MagicMock(
        installed=True, logged_in=False, detail="not logged in"
    )
    available, _ = codex_backend.verify()
    assert available is False


@patch("integrations.coding_agent.cursor_backend.CursorAdapter")
def test_cursor_verify_unclear_auth_counts_as_available(mock_cls: MagicMock) -> None:
    mock_cls.return_value.detect.return_value = MagicMock(
        installed=True, logged_in=None, detail="auth unclear"
    )
    available, _ = cursor_backend.verify()
    assert available is True
