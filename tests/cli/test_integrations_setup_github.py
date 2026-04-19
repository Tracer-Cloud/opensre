"""Tests for legacy `opensre integrations setup github` flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from app.cli.__main__ import cli
from app.integrations.cli import _setup_github, cmd_setup
from app.integrations.github_mcp import GitHubMCPValidationResult


def _upsert_should_not_run(*_a: object, **_k: object) -> None:
    raise AssertionError("upsert_integration should not be called when validation fails")


def test_setup_github_prints_connected_and_saves_on_validation_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_setup_github validates before saving and prints identity + detail on success."""

    answers = iter(["2", "https://api.githubcopilot.com/mcp/", "ghp_x", "repos,issues"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("app.integrations.cli._p", fake_p)
    monkeypatch.setattr("app.integrations.cli._prompt_github_repo_report_level", lambda: "full")
    monkeypatch.setattr(
        "app.integrations.cli.questionary.select",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: "auto"})(),
    )

    monkeypatch.setattr(
        "app.integrations.cli.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail=(
                "OK @devuser; repos=2; owners=Tracer-Cloud,acme; "
                "examples=Tracer-Cloud/opensre,acme/demo; mcp_tools=9"
            ),
            authenticated_user="devuser",
            repo_access_count=2,
            repo_access_scope_owners=("Tracer-Cloud", "acme"),
            repo_access_samples=("Tracer-Cloud/opensre", "acme/demo"),
        ),
    )

    saved: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        "app.integrations.cli.upsert_integration",
        lambda service, entry: saved.append((service, entry)),
    )

    _setup_github()

    out = capsys.readouterr().out
    assert "Validating GitHub MCP integration" in out
    assert "Configuration validation: succeeded" in out
    assert "@devuser" in out
    assert "Repositories returned" in out
    assert "Tracer-Cloud/opensre" in out
    assert saved == [
        (
            "github",
            {
                "credentials": {
                    "mode": "streamable-http",
                    "url": "https://api.githubcopilot.com/mcp/",
                    "auth_token": "ghp_x",
                    "toolsets": ["repos", "issues"],
                },
            },
        ),
    ]


def test_setup_github_exits_without_save_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(["2", "https://api.githubcopilot.com/mcp/", "", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("app.integrations.cli._p", fake_p)
    monkeypatch.setattr(
        "app.integrations.cli.questionary.select",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: "auto"})(),
    )
    monkeypatch.setattr(
        "app.integrations.cli.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=False,
            detail="GitHub MCP connected, but authentication failed: bad token",
            failure_category="authentication",
        ),
    )
    monkeypatch.setattr("app.integrations.cli.upsert_integration", _upsert_should_not_run)

    with pytest.raises(SystemExit) as exc:
        _setup_github()
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "Configuration validation: failed" in out
    assert "Failure type:" in out
    assert "authentication failed" in out


def test_cmd_setup_github_skips_saved_line_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """cmd_setup must not print success/saved after a failed GitHub validation."""

    answers = iter(["2", "https://api.githubcopilot.com/mcp/", "x", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("app.integrations.cli._p", fake_p)
    monkeypatch.setattr(
        "app.integrations.cli.questionary.select",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: "auto"})(),
    )
    monkeypatch.setattr(
        "app.integrations.cli.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=False,
            detail="validation failed for test",
            failure_category="connectivity",
        ),
    )
    monkeypatch.setattr("app.integrations.cli.upsert_integration", _upsert_should_not_run)

    with pytest.raises(SystemExit) as exc:
        cmd_setup("github")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Configuration validation: failed" in out
    assert "Saved" not in out


def test_cmd_setup_github_prints_saved_after_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Full cmd_setup('github') prints validation, Saved line, and does not duplicate handlers."""

    answers = iter(["2", "https://api.githubcopilot.com/mcp/", "tok", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("app.integrations.cli._p", fake_p)
    monkeypatch.setattr("app.integrations.cli._prompt_github_repo_report_level", lambda: "standard")
    monkeypatch.setattr(
        "app.integrations.cli.questionary.select",
        lambda *_a, **_k: type("X", (), {"ask": lambda *_aa, **_kk: "auto"})(),
    )
    monkeypatch.setattr(
        "app.integrations.cli.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail="OK @u; repos=0; owners=-; examples=-; mcp_tools=5",
            authenticated_user="u",
            repo_access_count=0,
            repo_access_scope_owners=(),
            repo_access_samples=(),
        ),
    )
    monkeypatch.setattr("app.integrations.cli.upsert_integration", lambda *_a, **_k: None)

    cmd_setup("github")
    out = capsys.readouterr().out
    assert "Configuration validation: succeeded" in out
    assert "@u" in out
    assert "Saved" in out


def test_integrations_setup_github_cli_invokes_cmd_setup() -> None:
    runner = CliRunner()
    with patch("app.integrations.cli.cmd_setup") as mock_cmd:
        result = runner.invoke(cli, ["integrations", "setup", "github"])
    assert result.exit_code == 0
    mock_cmd.assert_called_once_with("github")
