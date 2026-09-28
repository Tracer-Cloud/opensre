"""The onboarding picker uses the shared guided GitHub flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from surfaces.cli.wizard.configurators import github


def test_onboarding_delegates_to_shared_github_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(github, "setup_github", lambda: calls.append(1))
    monkeypatch.setattr(github, "PROJECT_ENV_PATH", Path("/tmp/opensre-test.env"))

    assert github._configure_github_mcp() == (
        "GitHub MCP",
        "/tmp/opensre-test.env",
    )
    assert calls == [1]
