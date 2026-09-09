"""Tests for the GitHub security and quality remediation action tool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from core.agent_harness.tools.tool_context import (
    ACTION_TOOL_CONTEXT_RESOURCE_KEY,
    ActionToolScope,
)
from core.tool.contracts import AgentToolContext, RegisteredTool
from integrations.coding_agent import CodingResult
from integrations.github.client import GitHubRestClient
from integrations.github.pull_requests import PullRequest
from integrations.github.tools.security_fix.context import (
    SecurityAlertContext,
    gather_security_alert_context,
    parse_security_alert_url,
)
from integrations.github.tools.security_fix.errors import (
    ERR_ALERT_NOT_FOUND,
    ERR_CONFIRMATION_DENIED,
    ERR_NO_AUTOFIXABLE_FINDING,
    ERR_UNSUPPORTED_ALERT_TYPE,
    GitHubSecurityFixError,
)
from integrations.github.tools.security_fix.runner import (
    ensure_ship_ready,
    run_fix,
    run_security_fix,
)
from integrations.github.tools.security_fix.ship import ShipResult, ship_security_fix
from integrations.github.tools.security_fix.tool import (
    _github_security_fix_available,
    fix_github_security_alert,
)
from tests.tools.conftest import BaseToolContract
from tools.registry import clear_tool_registry_cache, get_registered_tool_map, get_registered_tools

_CTX = SecurityAlertContext(
    owner="acme",
    repo="app",
    alert_type="dependabot",
    number=12,
    summary="Django vulnerable",
    url="https://github.com/acme/app/security/dependabot/12",
    task="Fix Dependabot alert.",
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _registered(tool: Any) -> RegisteredTool:
    return tool.__opensre_registered_tool__


class TestGitHubSecurityFixContract(BaseToolContract):
    def get_tool_under_test(self) -> RegisteredTool:
        return _registered(fix_github_security_alert)


def test_parse_security_alert_url_supports_common_github_urls() -> None:
    dependabot = parse_security_alert_url("https://github.com/acme/app/security/dependabot/12")
    assert dependabot is not None
    assert (dependabot.owner, dependabot.repo, dependabot.alert_type, dependabot.number) == (
        "acme",
        "app",
        "dependabot",
        12,
    )

    code = parse_security_alert_url("https://github.com/acme/app/code-scanning/7")
    assert code is not None
    assert code.alert_type == "code_scanning"
    assert code.number == 7

    code_page = parse_security_alert_url(
        "https://github.com/acme/app/security/code-scanning?query=is%3Aopen"
    )
    assert code_page is not None
    assert code_page.alert_type == "code_scanning"
    assert code_page.number is None

    code_alerts_page = parse_security_alert_url("https://github.com/acme/app/code-scanning/alerts")
    assert code_alerts_page is not None
    assert code_alerts_page.alert_type == "code_scanning"
    assert code_alerts_page.number is None

    code_page_with_sentence_punctuation = parse_security_alert_url(
        "https://github.com/acme/app/security/code-scanning."
    )
    assert code_page_with_sentence_punctuation is not None
    assert code_page_with_sentence_punctuation.alert_type == "code_scanning"
    assert code_page_with_sentence_punctuation.number is None

    quality_page = parse_security_alert_url("https://github.com/acme/app/security/quality")
    assert quality_page is not None
    assert quality_page.alert_type == "code_quality"
    assert quality_page.number is None

    quality_finding = parse_security_alert_url(
        "https://github.com/acme/app/code-quality/findings/42"
    )
    assert quality_finding is not None
    assert quality_finding.alert_type == "code_quality"
    assert quality_finding.number == 42

    quality_ui = parse_security_alert_url(
        "https://github.com/acme/app/security/quality/findings/42"
    )
    assert quality_ui is not None
    assert quality_ui.alert_type == "code_quality"
    assert quality_ui.number == 42


def test_available_when_github_token_present(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert _github_security_fix_available({}) is False

    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert _github_security_fix_available({}) is True


def test_available_with_configured_auth_token(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)

    assert _github_security_fix_available({"github": {"auth_token": "store-token"}}) is True


def test_ship_gate_uses_injected_token(monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_MCP_AUTH_TOKEN", raising=False)

    with patch("integrations.github.tools.security_fix.runner.ensure_git_repo") as ensure_git:
        ensure_ship_ready("/ws", github_token="store-token")

    ensure_git.assert_called_once_with("/ws")


def test_gather_dependabot_context_builds_coding_task() -> None:
    alert = {
        "number": 12,
        "html_url": "https://github.com/acme/app/security/dependabot/12",
        "dependency": {
            "package": {"ecosystem": "pip", "name": "django"},
            "manifest_path": "requirements.txt",
        },
        "security_vulnerability": {
            "severity": "high",
            "vulnerable_version_range": "< 4.2.20",
            "first_patched_version": {"identifier": "4.2.20"},
        },
        "security_advisory": {
            "summary": "Django vulnerable",
            "identifiers": [{"value": "GHSA-1234"}],
        },
    }
    with patch.object(GitHubRestClient, "request", return_value=alert):
        ctx = gather_security_alert_context(
            owner="acme",
            repo="app",
            alert_type="dependabot",
            alert_number=12,
            github_token="tok",
        )

    assert ctx.alert_type == "dependabot"
    assert ctx.number == 12
    assert "requirements.txt" in ctx.task
    assert "4.2.20" in ctx.task
    assert "GHSA-1234" in ctx.task


def test_gather_code_quality_context_builds_coding_task() -> None:
    finding = {
        "number": 42,
        "url": "https://api.github.com/repos/acme/app/code-quality/findings/42",
        "rule": {
            "id": "py/unused-import",
            "title": "Unused import",
            "description": "Imported name is not used.",
            "help": "Remove imports that are no longer needed.",
            "severity": "note",
            "category": "maintainability",
        },
        "location": {
            "path": "app/main.py",
            "start_line": 9,
            "start_column": 1,
            "end_line": 9,
            "end_column": 14,
        },
        "message": {"text": "This import is unused."},
    }
    with patch.object(GitHubRestClient, "request", return_value=finding) as request:
        ctx = gather_security_alert_context(
            owner="acme",
            repo="app",
            alert_type="code_quality",
            alert_number=42,
            github_token="tok",
        )

    assert ctx.alert_type == "code_quality"
    assert ctx.number == 42
    assert ctx.summary == "Unused import"
    assert ctx.url == "https://github.com/acme/app/security/quality/findings/42"
    assert "api.github.com" not in ctx.url
    assert "api.github.com" not in ctx.task
    assert "https://github.com/acme/app/security/quality/findings/42" in ctx.task
    assert "app/main.py:9" in ctx.task
    assert "maintainability" in ctx.task
    assert "This import is unused." in ctx.task
    assert request.call_args.kwargs["api_version"] == "2026-03-10"


def test_gather_code_scanning_page_url_targets_code_scanning_alerts() -> None:
    alert = {
        "number": 7,
        "html_url": "https://github.com/acme/app/security/code-scanning/7",
        "rule": {
            "id": "py/stack-trace-exposure",
            "description": "Stack trace exposure",
            "security_severity_level": "high",
            "help": "Return a generic error on external surfaces.",
        },
        "most_recent_instance": {
            "location": {"path": "gateway/web/webapp.py", "start_line": 20},
            "message": {"text": "Exception details are exposed in an HTTP response."},
        },
    }
    paths: list[str] = []

    def fake_paginate(_self: GitHubRestClient, path: str, **_kwargs: Any) -> list[dict[str, Any]]:
        paths.append(path)
        if path.endswith("/code-scanning/alerts"):
            return [alert]
        if path.endswith("/code-scanning/alerts/7/instances"):
            return []
        raise AssertionError(f"unexpected path: {path}")

    with patch.object(GitHubRestClient, "paginate", fake_paginate):
        ctx = gather_security_alert_context(
            alert_url="https://github.com/acme/app/security/code-scanning",
            github_token="tok",
        )

    assert ctx.alert_type == "code_scanning"
    assert ctx.number == 7
    assert ctx.summary == "Stack trace exposure"
    assert "gateway/web/webapp.py:20" in ctx.task
    assert paths == [
        "/repos/acme/app/code-scanning/alerts",
        "/repos/acme/app/code-scanning/alerts/7/instances",
    ]


def test_gather_auto_selects_highest_severity_supported_alert() -> None:
    low = {
        "number": 1,
        "html_url": "https://github.com/acme/app/security/dependabot/1",
        "dependency": {"package": {"ecosystem": "pip", "name": "low"}},
        "security_vulnerability": {"severity": "low"},
        "security_advisory": {"summary": "low issue"},
    }
    high = {
        "number": 2,
        "html_url": "https://github.com/acme/app/security/dependabot/2",
        "dependency": {"package": {"ecosystem": "pip", "name": "high"}},
        "security_vulnerability": {"severity": "high"},
        "security_advisory": {"summary": "high issue"},
    }
    quality_error = {
        "number": 42,
        "url": "https://api.github.com/repos/acme/app/code-quality/findings/42",
        "rule": {
            "id": "py/super-not-called",
            "title": "Missing call to superclass initializer",
            "severity": "note",
            "category": "reliability",
        },
        "location": {"path": "app/main.py", "start_line": 20},
        "message": {"text": "Call the superclass initializer."},
    }

    def fake_paginate(_self: GitHubRestClient, path: str, **_kwargs: Any) -> list[dict[str, Any]]:
        if path.endswith("/dependabot/alerts"):
            return [low, high]
        if path.endswith("/code-quality/findings"):
            return [quality_error]
        return []

    with patch.object(GitHubRestClient, "paginate", fake_paginate):
        ctx = gather_security_alert_context(owner="acme", repo="app", github_token="tok")

    assert ctx.alert_type == "dependabot"
    assert ctx.number == 2
    assert ctx.summary == "high issue"


def test_gather_auto_prefers_builtin_local_fix_when_requested() -> None:
    high = {
        "number": 2,
        "html_url": "https://github.com/acme/app/security/dependabot/2",
        "dependency": {"package": {"ecosystem": "pip", "name": "high"}},
        "security_vulnerability": {"severity": "high"},
        "security_advisory": {"summary": "high issue"},
    }
    local_fixable = {
        "number": 42,
        "url": "https://api.github.com/repos/acme/app/code-quality/findings/42",
        "rule": {
            "id": "py/unused-import",
            "title": "Unused import",
            "severity": "note",
            "category": "maintainability",
        },
        "location": {"path": "app/main.py", "start_line": 1},
        "message": {"text": "This import is unused."},
    }

    def fake_paginate(_self: GitHubRestClient, path: str, **_kwargs: Any) -> list[dict[str, Any]]:
        if path.endswith("/dependabot/alerts"):
            return [high]
        if path.endswith("/code-quality/findings"):
            return [local_fixable]
        return []

    with patch.object(GitHubRestClient, "paginate", fake_paginate):
        ctx = gather_security_alert_context(
            owner="acme",
            repo="app",
            github_token="tok",
            prefer_builtin_local_fix=True,
        )

    assert ctx.alert_type == "code_quality"
    assert ctx.number == 42
    assert ctx.summary == "Unused import"


def test_gather_auto_no_open_findings_reports_no_pr() -> None:
    with (
        patch.object(GitHubRestClient, "paginate", return_value=[]),
        patch(
            "integrations.github.tools.security_fix.runner.verify_coding_agent",
            return_value=(False, "not configured"),
        ),
        patch(
            "integrations.github.tools.security_fix.context.detect_git_remote_repo_scope",
            return_value=("acme", "app"),
        ),
    ):
        result = run_security_fix(
            owner="acme",
            repo="app",
            alert_type="auto",
            open_pr=True,
            github_token="tok",
        )

    assert result["success"] is False
    assert result["error_kind"] == ERR_ALERT_NOT_FOUND
    assert result["error"] == (
        "No open Dependabot, code-scanning, or Code Quality findings found in "
        "acme/app; no PR was created."
    )
    assert result["response_text"] == result["error"]


def test_gather_auto_with_only_unsupported_findings_reports_no_autofixable() -> None:
    dependabot = {
        "number": 2,
        "html_url": "https://github.com/acme/app/security/dependabot/2",
        "dependency": {"package": {"ecosystem": "pip", "name": "high"}},
        "security_vulnerability": {"severity": "high"},
        "security_advisory": {"summary": "high issue"},
    }
    quality_error = {
        "number": 42,
        "url": "https://api.github.com/repos/acme/app/code-quality/findings/42",
        "rule": {
            "id": "py/super-not-called",
            "title": "Missing call to superclass initializer",
            "severity": "note",
            "category": "reliability",
        },
        "location": {"path": "app/main.py", "start_line": 20},
        "message": {"text": "Call the superclass initializer."},
    }

    def fake_paginate(_self: GitHubRestClient, path: str, **_kwargs: Any) -> list[dict[str, Any]]:
        if path.endswith("/dependabot/alerts"):
            return [dependabot]
        if path.endswith("/code-quality/findings"):
            return [quality_error]
        return []

    with (
        patch.object(GitHubRestClient, "paginate", fake_paginate),
        patch(
            "integrations.github.tools.security_fix.runner.verify_coding_agent",
            return_value=(False, "not configured"),
        ),
        patch(
            "integrations.github.tools.security_fix.context.detect_git_remote_repo_scope",
            return_value=("acme", "app"),
        ),
    ):
        result = run_security_fix(
            owner="acme",
            repo="app",
            alert_type="auto",
            open_pr=True,
            github_token="tok",
        )

    assert result["success"] is False
    assert result["error_kind"] == ERR_NO_AUTOFIXABLE_FINDING
    assert result["error"] == (
        "Found open GitHub security or quality findings in acme/app, but none "
        "match OpenSRE's built-in auto-fixers; no PR was created."
    )
    assert result["response_text"] == result["error"]


def test_gather_auto_without_builtin_preference_can_select_unsupported_finding() -> None:
    quality_error = {
        "number": 42,
        "url": "https://api.github.com/repos/acme/app/code-quality/findings/42",
        "rule": {
            "id": "py/super-not-called",
            "title": "Missing call to superclass initializer",
            "severity": "note",
            "category": "reliability",
        },
        "location": {"path": "app/main.py", "start_line": 20},
        "message": {"text": "Call the superclass initializer."},
    }

    def fake_paginate(_self: GitHubRestClient, path: str, **_kwargs: Any) -> list[dict[str, Any]]:
        if path.endswith("/code-quality/findings"):
            return [quality_error]
        return []

    with patch.object(GitHubRestClient, "paginate", fake_paginate):
        ctx = gather_security_alert_context(
            owner="acme",
            repo="app",
            github_token="tok",
            prefer_builtin_local_fix=False,
        )

    assert ctx.alert_type == "code_quality"
    assert ctx.number == 42


def test_secret_scanning_alerts_are_refused_without_fetching() -> None:
    with patch.object(GitHubRestClient, "request") as request:
        result = run_security_fix(
            owner="acme",
            repo="app",
            alert_type="secret_scanning",
            alert_number=44,
            github_token="tok",
        )

    request.assert_not_called()
    assert result["success"] is False
    assert result["error_kind"] == ERR_UNSUPPORTED_ALERT_TYPE
    assert "rotate" in result["error"]


def test_run_fix_uses_builtin_local_code_quality_fixer_without_coding_agent(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    target = tmp_path / "app.py"
    target.write_text("import os\n\nprint('hello')\n", encoding="utf-8")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "base")
    ctx = SecurityAlertContext(
        owner="acme",
        repo="app",
        alert_type="code_quality",
        number=42,
        summary="Unused import",
        url="https://github.com/acme/app/security/quality",
        task="Fix unused import.",
        rule_id="py/unused-import",
        location_path="app.py",
        start_line=1,
    )

    with patch(
        "integrations.github.tools.security_fix.runner.verify_coding_agent",
        return_value=(False, "Pi CLI is not installed"),
    ):
        result = run_fix(ctx, str(tmp_path), model=None)

    assert result.success is True
    assert result.changed_files == ["app.py"]
    assert "import os" not in target.read_text(encoding="utf-8")
    assert "diff --git" in result.diff


def test_run_fix_without_local_support_names_the_coding_agent_requirement() -> None:
    ctx = SecurityAlertContext(
        owner="acme",
        repo="app",
        alert_type="code_quality",
        number=7,
        summary="Missing call to superclass initializer",
        url="https://github.com/acme/app/security/quality",
        task="Fix missing super init.",
        rule_id="py/missing-call-to-super-init",
        location_path="app.py",
        start_line=10,
    )

    with patch(
        "integrations.github.tools.security_fix.runner.verify_coding_agent",
        return_value=(False, "Pi CLI is not installed"),
    ):
        result = run_fix(ctx, "/workspace", model=None)

    assert result.success is False
    assert result.error is not None
    assert "No built-in local fixer supports" in result.error
    # The error is actionable: it names the coding agent CLIs that would let
    # OpenSRE fix the finding automatically, in one line.
    assert "coding agent CLI (Pi, Claude Code, Codex, or Cursor)" in result.error
    assert "\n" not in result.error


def test_gather_auto_prefer_builtin_raises_without_running_fix() -> None:
    quality_error = {
        "number": 42,
        "url": "https://api.github.com/repos/acme/app/code-quality/findings/42",
        "rule": {
            "id": "py/super-not-called",
            "title": "Missing call to superclass initializer",
            "severity": "note",
            "category": "reliability",
        },
        "location": {"path": "app/main.py", "start_line": 20},
        "message": {"text": "Call the superclass initializer."},
    }

    def fake_paginate(_self: GitHubRestClient, path: str, **_kwargs: Any) -> list[dict[str, Any]]:
        if path.endswith("/code-quality/findings"):
            return [quality_error]
        return []

    with patch.object(GitHubRestClient, "paginate", fake_paginate):
        try:
            gather_security_alert_context(
                owner="acme",
                repo="app",
                github_token="tok",
                prefer_builtin_local_fix=True,
            )
        except GitHubSecurityFixError as exc:
            assert exc.kind == ERR_NO_AUTOFIXABLE_FINDING
            assert "\n" not in exc.message
        else:
            raise AssertionError("expected no_autofixable_finding")


@patch("integrations.github.tools.security_fix.runner.run_fix")
@patch("integrations.github.tools.security_fix.runner.ensure_workspace_ready")
@patch("integrations.github.tools.security_fix.runner.ensure_cli_ready")
@patch(
    "integrations.github.tools.security_fix.runner.gather_security_alert_context",
    return_value=_CTX,
)
def test_run_security_fix_confirms_before_coding(
    _gather: MagicMock,
    _cli: MagicMock,
    _workspace: MagicMock,
    mock_run_fix: MagicMock,
) -> None:
    prompts: list[str] = []
    mock_run_fix.return_value = CodingResult(
        success=True,
        summary="Updated Django.",
        changed_files=["requirements.txt"],
        diff="diff --git a/requirements.txt b/requirements.txt\n",
    )

    result = run_security_fix(
        owner="acme",
        repo="app",
        alert_type="dependabot",
        alert_number=12,
        github_token="tok",
        confirm_fn=lambda prompt: prompts.append(prompt) or "y",
    )

    assert result["success"] is True
    assert result["changed_files"] == ["requirements.txt"]
    assert len(prompts) == 1
    assert "Apply an OpenSRE fix" in prompts[0]
    assert "coding agent" not in prompts[0].lower()


@patch("integrations.github.tools.security_fix.runner.run_ship")
@patch("integrations.github.tools.security_fix.runner.pre_coding_changes", return_value={})
@patch("integrations.github.tools.security_fix.runner.run_fix")
@patch("integrations.github.tools.security_fix.runner.ensure_ship_ready")
@patch("integrations.github.tools.security_fix.runner.ensure_workspace_ready")
@patch("integrations.github.tools.security_fix.runner.ensure_cli_ready")
@patch(
    "integrations.github.tools.security_fix.runner.gather_security_alert_context",
    return_value=_CTX,
)
def test_run_security_fix_denied_before_shipping_keeps_diff(
    _gather: MagicMock,
    _cli: MagicMock,
    _workspace: MagicMock,
    _ship_ready: MagicMock,
    mock_run_fix: MagicMock,
    _pre: MagicMock,
    mock_ship: MagicMock,
) -> None:
    answers = iter(["y", "n"])
    mock_run_fix.return_value = CodingResult(
        success=True,
        summary="Updated Django.",
        changed_files=["requirements.txt"],
        diff="diff --git a/requirements.txt b/requirements.txt\n",
    )

    result = run_security_fix(
        owner="acme",
        repo="app",
        alert_type="dependabot",
        alert_number=12,
        open_pr=True,
        github_token="tok",
        confirm_fn=lambda _prompt: next(answers),
    )

    assert result["success"] is False
    assert result["error_kind"] == ERR_CONFIRMATION_DENIED
    assert result["changed_files"] == ["requirements.txt"]
    assert "diff --git" in result["diff"]
    mock_ship.assert_not_called()


@patch(
    "integrations.github.tools.security_fix.runner.run_ship",
    return_value=ShipResult(
        branch_name="opensre/github-security-fix-dependabot-12-abc",
        pr=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
    ),
)
@patch("integrations.github.tools.security_fix.runner.pre_coding_changes", return_value={})
@patch("integrations.github.tools.security_fix.runner.run_fix")
@patch("integrations.github.tools.security_fix.runner.ensure_ship_ready")
@patch("integrations.github.tools.security_fix.runner.ensure_workspace_ready")
@patch("integrations.github.tools.security_fix.runner.ensure_cli_ready")
@patch(
    "integrations.github.tools.security_fix.runner.gather_security_alert_context",
    return_value=_CTX,
)
def test_run_security_fix_open_pr_success(
    _gather: MagicMock,
    _cli: MagicMock,
    _workspace: MagicMock,
    _ship_ready: MagicMock,
    mock_run_fix: MagicMock,
    _pre: MagicMock,
    _ship: MagicMock,
) -> None:
    mock_run_fix.return_value = CodingResult(
        success=True,
        summary="Updated Django.",
        changed_files=["requirements.txt"],
        diff="diff",
    )

    result = run_security_fix(
        owner="acme",
        repo="app",
        alert_type="dependabot",
        alert_number=12,
        open_pr=True,
        github_token="tok",
        confirm_fn=lambda _prompt: "y",
    )

    assert result["success"] is True
    assert result["branch_name"] == "opensre/github-security-fix-dependabot-12-abc"
    assert result["pr_url"] == "https://github.com/acme/app/pull/9"
    assert result["pr_number"] == 9
    assert result["response_text"] == (
        "Fixed dependabot finding #12 in acme/app and opened PR: https://github.com/acme/app/pull/9"
    )


@patch(
    "integrations.github.tools.security_fix.ship.open_pull_request",
    return_value=PullRequest(url="https://github.com/acme/app/pull/9", number=9),
)
@patch("integrations.github.tools.security_fix.ship.push_branch")
@patch("integrations.github.tools.security_fix.ship.commit_paths")
@patch("integrations.github.tools.security_fix.ship.create_branch")
@patch("integrations.github.tools.security_fix.ship.checkout_branch")
@patch("integrations.github.tools.security_fix.ship.current_branch", return_value="main")
@patch("integrations.github.tools.security_fix.ship.default_branch", return_value="main")
@patch(
    "integrations.github.tools.security_fix.ship.file_fingerprints",
    return_value={"requirements.txt": "after"},
)
@patch(
    "integrations.github.tools.security_fix.ship.changed_paths", return_value=["requirements.txt"]
)
@patch("integrations.github.tools.security_fix.ship.ensure_git_repo")
@patch("integrations.github.tools.security_fix.ship.short_head", return_value="abc123")
@patch("integrations.github.tools.security_fix.ship.resolve_github_token", return_value="env-token")
def test_ship_security_fix_uses_resolved_token_for_git_and_pr(
    _resolve_token: MagicMock,
    _short_head: MagicMock,
    _ensure_git_repo: MagicMock,
    _changed_paths: MagicMock,
    _file_fingerprints: MagicMock,
    mock_default_branch: MagicMock,
    _current_branch: MagicMock,
    _checkout_branch: MagicMock,
    _create_branch: MagicMock,
    _commit_paths: MagicMock,
    mock_push_branch: MagicMock,
    mock_open_pull_request: MagicMock,
) -> None:
    coding_result = CodingResult(
        success=True,
        summary="Updated Django.",
        changed_files=["requirements.txt"],
        diff="diff",
    )

    ship = ship_security_fix("/ws", ctx=_CTX, result=coding_result)

    assert ship.pr.url == "https://github.com/acme/app/pull/9"
    assert ship.branch_name == "opensre/github-security-fix-dependabot-12-abc123"
    mock_default_branch.assert_called_once_with("/ws", token="env-token")
    mock_push_branch.assert_called_once_with(
        "/ws",
        "opensre/github-security-fix-dependabot-12-abc123",
        base_default="main",
        token="env-token",
    )
    assert mock_open_pull_request.call_args.kwargs["github_token"] == "env-token"


def test_tool_passes_repl_confirmation_function() -> None:
    def confirm(_prompt: str) -> str:
        return "y"

    agent_context = AgentToolContext(
        resolved_integrations={},
        resources={
            ACTION_TOOL_CONTEXT_RESOURCE_KEY: ActionToolScope(
                session=object(),
                console=SimpleNamespace(),
                confirm_fn=confirm,
            )
        },
    )

    with patch(
        "integrations.github.tools.security_fix.tool.run_security_fix",
        return_value={"success": True},
    ) as runner:
        result = fix_github_security_alert(context=agent_context)

    assert result == {"success": True}
    assert runner.call_args.kwargs["confirm_fn"] is confirm


def test_registry_discovers_security_fix_on_action_surface() -> None:
    clear_tool_registry_cache()
    action = get_registered_tool_map("action")
    chat = get_registered_tool_map("chat")

    tool = action["fix_github_security_alert"]
    assert tool.surfaces == ("action",)
    assert tool.requires_approval is True
    assert tool.side_effect_level == "mutating"
    assert "fix_github_security_alert" not in chat


def test_skill_guidance_attaches_to_security_fix_tool() -> None:
    clear_tool_registry_cache()
    tools_by_name = {tool.name: tool for tool in get_registered_tools()}
    tool = tools_by_name["fix_github_security_alert"]

    assert "Workflow guidance:" in tool.description
    assert '<skill name="operating-github-security-fixer"' in tool.skill_guidance
    assert "Secret-scanning alerts are refused" in tool.skill_guidance
