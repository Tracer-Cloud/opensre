"""Workspace identity and capability-warning runtime facts."""

from __future__ import annotations

from config.constants.runtime_metadata import (
    OPENSRE_ALLOW_NETWORK_ENV,
    OPENSRE_WORKSPACE_REPO_ENV,
)
from config.runtime_metadata import build_runtime_metadata
from config.runtime_metadata.probes import (
    capability_warning_facts,
    workspace_identity_facts,
)
from core.agent_harness.prompts.runtime_facts import render_static_runtime_facts


def test_workspace_repo_from_env(monkeypatch) -> None:
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "Tracer-Cloud/opensre")
    assert workspace_identity_facts()["workspace_repo"] == "Tracer-Cloud/opensre"


def test_workspace_repo_normalizes_github_urls(monkeypatch) -> None:
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "git@github.com:Tracer-Cloud/opensre.git")
    assert workspace_identity_facts()["workspace_repo"] == "Tracer-Cloud/opensre"
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "https://github.com/acme/widgets.git")
    assert workspace_identity_facts()["workspace_repo"] == "acme/widgets"


def test_read_git_origin_identity_from_config_body() -> None:
    """Parser is pure (no ``.git`` writes) so sandboxes and CI can exercise it."""
    from config.runtime_metadata.probes import read_git_origin_identity

    assert (
        read_git_origin_identity('[remote "origin"]\n\turl = git@github.com:acme/from-git.git\n')
        == "acme/from-git"
    )
    assert read_git_origin_identity("[core]\n\trepositoryformatversion = 0\n") == ""


def test_read_git_origin_identity_prefers_upstream_over_a_fork_origin() -> None:
    from config.runtime_metadata.probes import read_git_origin_identity

    config = (
        '[remote "origin"]\n\turl = git@github.com:someone/opensre.git\n'
        '[remote "upstream"]\n\turl = https://github.com/Tracer-Cloud/opensre.git\n'
    )

    assert read_git_origin_identity(config) == "Tracer-Cloud/opensre"


def test_workspace_line_in_prompt_when_set(monkeypatch) -> None:
    monkeypatch.setenv(OPENSRE_WORKSPACE_REPO_ENV, "acme/widgets")
    facts = build_runtime_metadata()
    block = render_static_runtime_facts(facts)
    assert "this OpenSRE workspace default repo is acme/widgets" in block
    assert "treat “our” / “this repo” as acme/widgets" in block
    assert "session repository context can select another active repo" in block
    assert "does not limit how many repositories long-term memory" in block


def test_workspace_absence_line_when_unknown(monkeypatch) -> None:
    block = render_static_runtime_facts({"opensre_version": "0.1", "workspace_repo": ""})
    assert "no workspace git/GitHub repo was detected" in block


def test_capability_warnings_include_network_default(monkeypatch) -> None:
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    facts = capability_warning_facts({"curl": "", "bash": "/bin/bash", "sh": ""})
    assert facts["network_egress"] is False
    assert facts["shell_available"] is True
    assert any("curl" in w for w in facts["capability_warnings"])
    assert any("network egress" in w for w in facts["capability_warnings"])


def test_capability_warnings_include_missing_shell(monkeypatch) -> None:
    # Arrange
    monkeypatch.delenv(OPENSRE_ALLOW_NETWORK_ENV, raising=False)
    tools = {"bash": "", "sh": ""}

    # Act
    facts = capability_warning_facts(tools)

    # Assert
    assert facts["shell_available"] is False
    assert any("no interactive shell" in warning for warning in facts["capability_warnings"])


def test_capability_warnings_include_network_opt_in(monkeypatch) -> None:
    # Arrange
    monkeypatch.setenv(OPENSRE_ALLOW_NETWORK_ENV, "1")
    tools = {
        "curl": "/usr/bin/curl",
        "bash": "/bin/bash",
        "sh": "/bin/sh",
    }

    # Act
    facts = capability_warning_facts(tools)

    # Assert
    assert facts["network_egress"] is True
    assert not any("network egress" in warning for warning in facts["capability_warnings"])


def test_capability_warnings_line_in_prompt() -> None:
    block = render_static_runtime_facts(
        {
            "opensre_version": "0.1",
            "capability_warnings": ["curl is not on PATH", "network egress is blocked"],
        }
    )
    assert "capability warnings at boot: curl is not on PATH; network egress is blocked" in block


class TestRepoIdentityHostIsExact:
    """Only a real GitHub host may yield a bare ``owner/repo`` identity.

    Substring matching on "github.com" accepts any URL that merely contains it,
    so a remote pointing at an attacker host resolves to a clean-looking
    identity. That fact reaches prompts and reports, where it reads as the
    project's real repository.
    """

    def test_genuine_github_remotes_resolve(self) -> None:
        from config.runtime_metadata.probes import _normalize_repo_identity

        for url in (
            "https://github.com/Tracer-Cloud/opensre",
            "https://github.com/Tracer-Cloud/opensre.git",
            "git@github.com:Tracer-Cloud/opensre.git",
            "ssh://git@github.com/Tracer-Cloud/opensre",
        ):
            assert _normalize_repo_identity(url) == "Tracer-Cloud/opensre", url

    def test_host_containing_github_com_is_not_treated_as_github(self) -> None:
        from config.runtime_metadata.probes import _normalize_repo_identity

        # A planted identity that must never be reported as owner/repo.
        for url in (
            "https://evil.test/github.com/attacker/repo",
            "git@notgithub.com:attacker/repo",
            "https://github.com.attacker.test/attacker/repo",
        ):
            assert _normalize_repo_identity(url) != "attacker/repo", url

    def test_non_github_remotes_are_left_alone(self) -> None:
        from config.runtime_metadata.probes import _normalize_repo_identity

        url = "https://gitlab.com/org/repo"
        assert _normalize_repo_identity(url) == url
