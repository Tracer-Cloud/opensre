"""Tests for the guided ``opensre integrations setup github`` flow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

import integrations.setup_flow as setup_flow
from config.constants import INTEGRATIONS_STORE_PATH_ENV
from integrations.cli import cmd_setup
from integrations.github.cli_setup import _github_browser_auth_token, setup_github
from integrations.github.mcp import GitHubMCPValidationResult
from surfaces.cli.app import cli


def _prompt_answering(answer: object) -> object:
    """A questionary prompt double: calling it yields an object whose ``ask()`` returns ``answer``."""

    def _prompt(*_args: object, **_kwargs: object) -> object:
        return type("X", (), {"ask": lambda *_aa, **_kk: answer})()

    return _prompt


def _mock_github_setup_path(monkeypatch: pytest.MonkeyPatch, *, customize: bool) -> None:
    """Choose a GitHub setup path and keep automatic repository defaults."""

    def _select(message: str, *_args: object, **_kwargs: object) -> object:
        if message == "How would you like to connect?":
            return "customize" if customize else "recommended"
        if message == "Which repository view should we use to verify access?":
            return "auto"
        if message == "Filter repositories by visibility (best-effort)":
            return "any"
        raise AssertionError(f"Unexpected select prompt: {message}")

    monkeypatch.setattr("integrations.github.cli_setup._select", _select)


@pytest.fixture(autouse=True)
def _cancel_retry_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    def _cancel(*_args: object, **_kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr("integrations.github.cli_setup.TerminalSetupUI.choose", _cancel)


def _patch_apply_setup(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    """Patch ``apply_setup`` where the function-local import resolves it."""
    monkeypatch.setattr(setup_flow, "apply_setup", fake)


def test_github_browser_auth_falls_back_to_manual_token(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("integrations.github.cli_setup._github_browser_authorize", lambda: None)
    monkeypatch.setattr("integrations.github.cli_setup._p", lambda *_args, **_kwargs: "ghp_manual")

    assert _github_browser_auth_token() == "ghp_manual"
    assert "Falling back to manual token entry" in capsys.readouterr().out


def test_setup_github_can_cancel_at_connection_choice(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("integrations.github.cli_setup._select", lambda *_args, **_kwargs: None)

    with pytest.raises(SystemExit) as exc:
        setup_github()

    assert exc.value.code == 1
    assert "Aborted." in capsys.readouterr().out


def test_setup_github_prints_connected_and_saves_on_validation_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GitHub setup validates before saving and prints identity + detail on success."""

    answers = iter(["https://api.githubcopilot.com/mcp/", "repos,issues"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("integrations.github.cli_setup._p", fake_p)
    _mock_github_setup_path(monkeypatch, customize=True)
    monkeypatch.setattr(
        "integrations.github.cli_setup._setup_github_auth_token", lambda _mode: "ghp_x"
    )
    monkeypatch.setattr(
        "integrations.github.cli_setup._prompt_github_repo_report_level", lambda: "full"
    )

    monkeypatch.setattr(
        "integrations.github.mcp.validate_github_mcp_config",
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

    saved: list[dict] = []

    def _fake_apply(_spec: object, values: object) -> setup_flow.SetupOutcome:
        assert isinstance(values, dict)
        saved.append(values)
        return setup_flow.SetupOutcome(ok=True, detail="ok", env_path=Path("/tmp/.env"))

    _patch_apply_setup(monkeypatch, _fake_apply)

    setup_github()

    out = capsys.readouterr().out
    assert "Validating GitHub MCP integration" in out
    assert "Configuration validation: succeeded" in out
    assert "@devuser" in out
    assert "Repositories returned" in out
    assert "Tracer-Cloud/opensre" in out
    assert saved == [
        {
            "mode": "streamable-http",
            "url": "https://api.githubcopilot.com/mcp/",
            "auth_token": "ghp_x",
            "toolsets": "repos,issues",
            "username": "devuser",
        },
    ]


def test_setup_github_simple_path_uses_hosted_defaults(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Recommended setup signs in with hosted defaults and no advanced prompts."""

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        raise AssertionError("simple path should not call _p")

    def _no_level_prompt() -> str:
        raise AssertionError("simple path should not prompt for repo detail level")

    monkeypatch.setattr("integrations.github.cli_setup._p", fake_p)
    setup_choices: list[tuple[str, list[object], str]] = []

    def _select(message: str, choices: list[object], *, default: str) -> str:
        setup_choices.append((message, choices, default))
        return "recommended"

    monkeypatch.setattr("integrations.github.cli_setup._select", _select)
    monkeypatch.setattr(
        "integrations.github.cli_setup._github_browser_authorize", lambda: "gho_browser"
    )
    monkeypatch.setattr(
        "integrations.github.cli_setup._prompt_github_repo_report_level", _no_level_prompt
    )
    monkeypatch.setattr(
        "integrations.github.mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail="OK @u; repos=2; owners=acme; examples=acme/a; mcp_tools=5",
            authenticated_user="u",
            repo_access_count=2,
            repo_access_scope_owners=("acme",),
            repo_access_samples=("acme/a", "acme/b"),
            repo_access_probe_tool="list_starred_repositories",
        ),
    )

    saved: list[dict] = []

    def _fake_apply(_spec: object, values: object) -> setup_flow.SetupOutcome:
        assert isinstance(values, dict)
        saved.append(values)
        return setup_flow.SetupOutcome(ok=True, detail="ok", env_path=Path("/tmp/.env"))

    _patch_apply_setup(monkeypatch, _fake_apply)

    setup_github()

    out = capsys.readouterr().out
    assert out.count("Validating GitHub MCP integration") == 1
    assert len(setup_choices) == 1
    message, choices, default = setup_choices[0]
    assert message == "How would you like to connect?"
    assert [(choice.title, choice.value) for choice in choices] == [
        ("Sign in with GitHub (recommended)", "recommended"),
        ("Customize connection", "customize"),
    ]
    assert default == "recommended"
    # Concise summary: no access-source / starred / repo enumeration noise.
    assert "Access source" not in out
    assert "Starred" not in out
    assert saved == [
        {
            "mode": "streamable-http",
            "url": "https://api.githubcopilot.com/mcp/",
            "auth_token": "gho_browser",
            "toolsets": "repos,issues,pull_requests,actions,search",
            "username": "u",
        },
    ]


def test_setup_github_exits_without_save_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    answers = iter(["https://api.githubcopilot.com/mcp/", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("integrations.github.cli_setup._p", fake_p)
    _mock_github_setup_path(monkeypatch, customize=True)
    monkeypatch.setattr("integrations.github.cli_setup._setup_github_auth_token", lambda _mode: "")
    monkeypatch.setattr(
        "integrations.github.mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=False,
            detail="GitHub MCP connected, but authentication failed: bad token",
            failure_category="authentication",
        ),
    )

    def _apply_should_not_run(*_a: object, **_k: object) -> setup_flow.SetupOutcome:
        raise AssertionError("apply_setup should not be called when validation fails")

    _patch_apply_setup(monkeypatch, _apply_should_not_run)

    with pytest.raises(SystemExit) as exc:
        setup_github()
    assert exc.value.code == 1

    out = capsys.readouterr().out
    assert "Configuration validation: failed" in out
    assert "Failure type:" in out
    assert "authentication failed" in out


def test_setup_github_retries_validation_without_repeating_sign_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A temporary check failure retries with the accepted browser credential."""
    _mock_github_setup_path(monkeypatch, customize=False)
    auth_calls: list[int] = []

    def _authorize() -> str:
        auth_calls.append(1)
        return "gho_browser"

    monkeypatch.setattr("integrations.github.cli_setup._github_browser_authorize", _authorize)
    results = iter(
        [
            GitHubMCPValidationResult(
                ok=False,
                detail="Temporary connectivity failure",
                failure_category="connectivity",
            ),
            GitHubMCPValidationResult(
                ok=True,
                detail="OK @u; repos=1; owners=acme; examples=acme/a; mcp_tools=5",
                authenticated_user="u",
                repo_access_count=1,
                repo_access_scope_owners=("acme",),
                repo_access_samples=("acme/a",),
            ),
        ]
    )
    monkeypatch.setattr(
        "integrations.github.mcp.validate_github_mcp_config",
        lambda _config, **_kwargs: next(results),
    )
    monkeypatch.setattr(
        "integrations.github.cli_setup.TerminalSetupUI.choose",
        lambda *_args, **_kwargs: "retry",
    )
    monkeypatch.setattr(
        setup_flow,
        "apply_setup",
        lambda *_args, **_kwargs: setup_flow.SetupOutcome(
            ok=True,
            detail="ok",
            env_path=Path("/tmp/.env"),
        ),
    )

    setup_github()

    assert auth_calls == [1]


def test_cmd_setup_github_skips_saved_line_on_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """cmd_setup must not print success/saved after a failed GitHub validation."""

    answers = iter(["https://api.githubcopilot.com/mcp/", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("integrations.github.cli_setup._p", fake_p)
    _mock_github_setup_path(monkeypatch, customize=True)
    monkeypatch.setattr("integrations.github.cli_setup._setup_github_auth_token", lambda _mode: "x")
    monkeypatch.setattr(
        "integrations.github.mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=False,
            detail="validation failed for test",
            failure_category="connectivity",
        ),
    )

    def _apply_should_not_run(*_a: object, **_k: object) -> setup_flow.SetupOutcome:
        raise AssertionError("apply_setup should not be called when validation fails")

    _patch_apply_setup(monkeypatch, _apply_should_not_run)

    with pytest.raises(SystemExit) as exc:
        cmd_setup("github")
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "Configuration validation: failed" in out
    assert "Saved" not in out


def test_cmd_setup_github_prints_saved_after_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Full cmd_setup('github') prints validation, Saved line, and does not duplicate handlers."""

    store_path = tmp_path / "integrations.json"
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, str(store_path))
    monkeypatch.setattr("integrations.store.STORE_PATH", None)
    answers = iter(["https://api.githubcopilot.com/mcp/", "repos"])

    def fake_p(_label: str, default: str = "", secret: bool = False) -> str:
        return next(answers)

    monkeypatch.setattr("integrations.github.cli_setup._p", fake_p)
    _mock_github_setup_path(monkeypatch, customize=True)
    monkeypatch.setattr(
        "integrations.github.cli_setup._setup_github_auth_token", lambda _mode: "tok"
    )
    monkeypatch.setattr(
        "integrations.github.cli_setup._prompt_github_repo_report_level", lambda: "standard"
    )
    monkeypatch.setattr(
        "integrations.github.mcp.validate_github_mcp_config",
        lambda _c, **_kwargs: GitHubMCPValidationResult(
            ok=True,
            detail="OK @u; repos=0; owners=-; examples=-; mcp_tools=5",
            authenticated_user="u",
            repo_access_count=0,
            repo_access_scope_owners=(),
            repo_access_samples=(),
        ),
    )

    def _fake_apply(_spec: object, _values: object) -> setup_flow.SetupOutcome:
        return setup_flow.SetupOutcome(ok=True, detail="ok", env_path=Path("/tmp/.env"))

    _patch_apply_setup(monkeypatch, _fake_apply)

    cmd_setup("github")
    out = capsys.readouterr().out
    assert "Configuration validation: succeeded" in out
    assert "@u" in out
    assert f"Saved → {store_path}" in out


def test_integrations_setup_github_cli_invokes_cmd_setup() -> None:
    runner = CliRunner()
    with (
        patch("surfaces.cli.commands.integrations.capture_integration_setup_started"),
        patch("surfaces.cli.commands.integrations.capture_integration_setup_completed"),
        patch("surfaces.cli.commands.integrations.capture_integration_verified"),
        patch("integrations.cli.cmd_setup") as mock_cmd,
        patch("integrations.cli.cmd_verify", return_value=0),
    ):
        mock_cmd.return_value = "github"
        result = runner.invoke(cli, ["integrations", "setup", "github"])
    assert result.exit_code == 0
    mock_cmd.assert_called_once_with("github")
