from __future__ import annotations

from typing import cast

import pytest

from config.runbook_sources import RunbookSourceConfig
from core.domain.runbooks import RunbookSource
from infrastructure.harness_providers.runbooks import (
    clear_runbook_source_providers,
    register_runbook_source_provider,
    resolve_runbook_source,
)


@pytest.fixture(autouse=True)
def _clear_registry() -> None:
    clear_runbook_source_providers()


def test_registered_provider_builds_matching_source() -> None:
    sentinel = cast("RunbookSource", object())
    config = RunbookSourceConfig(
        name="platform-runbooks",
        provider="github",
        repository="acme/operations",
    )

    register_runbook_source_provider(
        "github",
        lambda _config, _resolved: sentinel,
    )

    assert resolve_runbook_source(config, {"github": {}}) is sentinel


def test_unknown_or_unavailable_provider_returns_none() -> None:
    config = RunbookSourceConfig(
        name="platform-runbooks",
        provider="github",
        repository="acme/operations",
    )
    register_runbook_source_provider("github", lambda _config, _resolved: None)

    assert resolve_runbook_source(config, {}) is None
    clear_runbook_source_providers()
    assert resolve_runbook_source(config, {}) is None
