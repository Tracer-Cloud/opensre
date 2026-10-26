"""Onboarding opens app setup without reporting a saved connection."""

from surfaces.cli.wizard.configurators import github


def test_onboarding_handoff_remains_pending(monkeypatch):
    calls = []
    monkeypatch.setattr(github, "setup_github", lambda: calls.append(1))
    assert github._configure_github_app() == ("", "")
    assert calls == [1]
