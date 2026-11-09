from __future__ import annotations

from pathlib import Path

import pytest

from config import local_settings
from config.runbook_sources import (
    RunbookSourceConfig,
    RunbookSourceConfigError,
    add_runbook_source,
    load_runbook_sources,
    remove_runbook_source,
)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(local_settings.paths, "OPENSRE_HOME_DIR", tmp_path)


def test_add_and_load_source_preserves_other_settings() -> None:
    local_settings.save_local_settings({"interactive": {"theme": "default"}})

    add_runbook_source(
        RunbookSourceConfig(
            name="platform-runbooks",
            provider="github",
            repository="acme/operations",
            ref="main",
            manifest=".opensre/runbooks.yaml",
        )
    )

    assert load_runbook_sources() == (
        RunbookSourceConfig(
            name="platform-runbooks",
            provider="github",
            repository="acme/operations",
            ref="main",
            manifest=".opensre/runbooks.yaml",
        ),
    )
    assert local_settings.read_section("interactive") == {"theme": "default"}


def test_duplicate_source_name_is_rejected() -> None:
    source = RunbookSourceConfig(
        name="platform-runbooks",
        provider="github",
        repository="acme/operations",
    )
    add_runbook_source(source)

    with pytest.raises(RunbookSourceConfigError, match="already exists"):
        add_runbook_source(source)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("name", "../platform"),
        ("repository", "missing-owner"),
        ("repository", "acme/../secrets"),
        ("manifest", "/etc/passwd"),
        ("manifest", "../runbooks.yaml"),
        ("manifest", "runbooks/\nmanifest.yaml"),
        ("ref", ""),
    ),
)
def test_invalid_source_values_are_rejected(field: str, value: str) -> None:
    values = {
        "name": "platform-runbooks",
        "provider": "github",
        "repository": "acme/operations",
        "ref": "main",
        "manifest": ".opensre/runbooks.yaml",
    }
    values[field] = value

    with pytest.raises(ValueError):
        RunbookSourceConfig.model_validate(values)


def test_remove_source_returns_whether_it_existed() -> None:
    add_runbook_source(
        RunbookSourceConfig(
            name="platform-runbooks",
            provider="github",
            repository="acme/operations",
        )
    )

    assert remove_runbook_source("platform-runbooks") is True
    assert remove_runbook_source("platform-runbooks") is False
    assert load_runbook_sources() == ()


def test_invalid_persisted_source_fails_closed() -> None:
    local_settings.save_local_settings(
        {
            "runbooks": {
                "sources": [
                    {
                        "name": "platform-runbooks",
                        "provider": "github",
                        "repository": "invalid",
                    }
                ]
            }
        }
    )

    with pytest.raises(RunbookSourceConfigError, match="Invalid runbook source"):
        load_runbook_sources()
