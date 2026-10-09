"""Tests for GitHub CLI detection, version probing, and authentication status inspection."""

from __future__ import annotations

import subprocess

import pytest

from integrations.github.cli_probe import (
    GitHubCLIAuthStatus,
    GitHubCLIHostAccount,
    _parse_gh_auth_json,
    _parse_gh_auth_text,
    detect_github_cli,
    probe_gh_auth,
    resolve_gh_binary,
)


def test_resolve_gh_binary_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_BIN", "/custom/bin/gh")
    monkeypatch.setattr(
        "integrations.llm_cli.binary_resolver.is_runnable_binary",
        lambda path: path == "/custom/bin/gh",
    )
    assert resolve_gh_binary() == "/custom/bin/gh"


def test_resolve_gh_binary_from_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_BIN", raising=False)
    monkeypatch.setattr(
        "integrations.llm_cli.binary_resolver.shutil.which",
        lambda name: "/usr/bin/gh" if name == "gh" else None,
    )
    monkeypatch.setattr(
        "integrations.llm_cli.binary_resolver.is_runnable_binary",
        lambda path: path == "/usr/bin/gh",
    )
    assert resolve_gh_binary() == "/usr/bin/gh"


def test_resolve_gh_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_BIN", raising=False)
    monkeypatch.setattr("integrations.llm_cli.binary_resolver.shutil.which", lambda _name: None)
    monkeypatch.setattr(
        "integrations.llm_cli.binary_resolver.is_runnable_binary", lambda _path: False
    )
    monkeypatch.setattr("integrations.github.cli_probe._fallback_gh_paths", lambda: [])
    assert resolve_gh_binary() is None


def test_detect_github_cli_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.github.cli_probe.resolve_gh_binary", lambda: None)
    res = detect_github_cli()
    assert not res.installed
    assert res.binary_path is None
    assert "not found" in res.detail


def test_detect_github_cli_version_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.github.cli_probe.resolve_gh_binary", lambda: "/usr/bin/gh")
    monkeypatch.setattr(
        "integrations.github.cli_probe.run_version_probe",
        lambda _bin, **_kw: ("gh version 2.45.0 (2024-03-04)", None),
    )
    monkeypatch.setattr(
        "integrations.github.cli_probe.probe_gh_auth",
        lambda _bin, **_kw: (
            GitHubCLIAuthStatus.AUTHENTICATED,
            GitHubCLIHostAccount("github.com", "octocat", True),
            (GitHubCLIHostAccount("github.com", "octocat", True),),
            "Authenticated as @octocat on github.com.",
        ),
    )
    res = detect_github_cli()
    assert res.installed
    assert res.version == "2.45.0"
    assert res.auth_status == GitHubCLIAuthStatus.AUTHENTICATED
    assert res.active_account is not None
    assert res.active_account.username == "octocat"
    assert res.active_account.hostname == "github.com"


def test_detect_github_cli_version_fails_or_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("integrations.github.cli_probe.resolve_gh_binary", lambda: "/usr/bin/gh")
    monkeypatch.setattr(
        "integrations.github.cli_probe.run_version_probe",
        lambda _bin, **_kw: (None, "Could not run `/usr/bin/gh --version`: Command timed out"),
    )
    res = detect_github_cli()
    assert not res.installed
    assert res.binary_path == "/usr/bin/gh"
    assert "timed out" in res.detail


def test_parse_gh_auth_json_single_authenticated_host() -> None:
    raw = """{
      "hosts": {
        "github.com": [
          {
            "state": "success",
            "active": true,
            "login": "mona",
            "tokenSource": "keyring"
          }
        ]
      }
    }"""
    status, active, all_acc, detail = _parse_gh_auth_json(raw)
    assert status == GitHubCLIAuthStatus.AUTHENTICATED
    assert active is not None
    assert active.username == "mona"
    assert active.hostname == "github.com"
    assert len(all_acc) == 1
    assert "Authenticated as @mona on github.com." in detail


def test_parse_gh_auth_json_multiple_hosts_and_enterprise() -> None:
    raw = """{
      "hosts": {
        "github.com": [
          {
            "state": "success",
            "active": true,
            "login": "mona_personal"
          }
        ],
        "github.enterprise.acme.com": [
          {
            "state": "success",
            "active": true,
            "login": "mona_work"
          }
        ]
      }
    }"""
    # Probe targeting Enterprise host
    status, active, all_acc, detail = _parse_gh_auth_json(
        raw, target_host="github.enterprise.acme.com"
    )
    assert status == GitHubCLIAuthStatus.AUTHENTICATED
    assert active is not None
    assert active.username == "mona_work"
    assert active.hostname == "github.enterprise.acme.com"
    assert len(all_acc) == 2

    # Probe without target host defaults to active account
    status_default, active_default, _, _ = _parse_gh_auth_json(raw)
    assert status_default == GitHubCLIAuthStatus.AUTHENTICATED
    assert active_default is not None
    assert active_default.username == "mona_personal"


def test_parse_gh_auth_json_no_hosts() -> None:
    raw = '{"hosts": {}}'
    status, active, all_acc, detail = _parse_gh_auth_json(raw)
    assert status == GitHubCLIAuthStatus.NOT_AUTHENTICATED
    assert active is None
    assert not all_acc


def test_parse_gh_auth_text_authenticated() -> None:
    text = """
github.com
  ✓ Logged in to github.com account hubot (keyring)
  - Active account: true
  - Git operations protocol: https
"""
    status, active, all_acc, detail = _parse_gh_auth_text(text)
    assert status == GitHubCLIAuthStatus.AUTHENTICATED
    assert active is not None
    assert active.username == "hubot"
    assert active.hostname == "github.com"


def test_parse_gh_auth_text_unauthenticated() -> None:
    text = "You are not logged into any GitHub hosts. Run gh auth login to authenticate."
    status, active, all_acc, detail = _parse_gh_auth_text(text)
    assert status == GitHubCLIAuthStatus.NOT_AUTHENTICATED
    assert active is None


def test_parse_gh_auth_text_unknown_ambiguous() -> None:
    text = "Some internal daemon connection error or unexpected message"
    status, active, all_acc, detail = _parse_gh_auth_text(text)
    assert status == GitHubCLIAuthStatus.UNKNOWN
    assert active is None


def test_probe_gh_auth_subcommand_failure_preserves_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        # Both JSON and text fail with exit code 2 and unexpected error
        return subprocess.CompletedProcess(
            args=argv,
            returncode=2,
            stdout="",
            stderr="fatal: corrupted configuration file or permission denied",
        )

    monkeypatch.setattr("integrations.github.cli_probe.subprocess.run", fake_run)
    status, active, all_acc, detail = probe_gh_auth("/usr/bin/gh")
    assert status == GitHubCLIAuthStatus.UNKNOWN
    assert active is None
    assert "exited with code 2" in detail or "permission denied" in detail


def test_probe_gh_auth_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["gh", "auth", "status"], timeout=5.0)

    monkeypatch.setattr("integrations.github.cli_probe.subprocess.run", fake_run)
    status, active, all_acc, detail = probe_gh_auth("/usr/bin/gh")
    assert status == GitHubCLIAuthStatus.UNKNOWN
    assert active is None
    assert "Failed to run `gh auth status`" in detail
