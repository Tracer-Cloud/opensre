"""Contracts for README install instructions."""

from __future__ import annotations

from pathlib import Path

_README = Path(__file__).resolve().parents[2] / "README.md"


def test_readme_presents_main_as_default_install_channel() -> None:
    source = _README.read_text()

    assert "fetches the latest build from `main`" in source
    assert "curl -fsSL https://install.opensre.com | bash -s -- -gh" in source
    assert "& ([scriptblock]::Create((irm https://install.opensre.com/install.ps1))) -gh" in source
    assert "brew install" not in source
    assert "irm https://install.opensre.com | iex" not in source
    assert "Latest stable release:" not in source
    assert "Latest build from `main`:" not in source
    assert "instead of the latest stable release" not in source
