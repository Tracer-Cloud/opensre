"""Chat surfaces send the user to the web app page that connects an integration."""

from __future__ import annotations

import pytest

from integrations.setup import web_setup_url


def test_hosted_gateway_links_github_to_its_settings_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hosted gateway has no account login; the control plane injects the web app URL.
    monkeypatch.setenv("OPENSRE_WEBAPP_URL", "https://app.example.com/")

    assert web_setup_url("github") == "https://app.example.com/settings/github"
    assert web_setup_url("google_docs") == "https://app.example.com/integrations/google-docs"


def test_no_web_app_means_no_link(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENSRE_WEBAPP_URL", raising=False)

    assert web_setup_url("github") == ""
