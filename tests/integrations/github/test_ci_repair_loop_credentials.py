"""The repair loop must find the GitHub token the integrations store holds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.constants import GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV
from config.constants.tenancy import INTEGRATIONS_STORE_PATH_ENV
from integrations.github.tools.ci_repair_loop.credentials import configured_token


def test_the_stored_token_is_used_when_no_env_token_is_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arrange: a hydrated store (the shape the gateway writes at boot) and no env tokens
    store = tmp_path / "integrations.json"
    record = {
        "id": "1",
        "service": "github",
        "status": "active",
        "instances": [{"name": "default", "tags": {}, "credentials": {"auth_token": "ghp_stored"}}],
    }
    store.write_text(json.dumps({"version": 2, "integrations": [record]}))
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, str(store))
    for name in (GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV, GH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)

    # Act
    token = configured_token()

    # Assert
    assert token == "ghp_stored"
