"""Onboarding maps retired wizard modes and configures one integration."""

from __future__ import annotations

from typing import Any

import pytest

from surfaces.cli.wizard import _integration_configurators as configurators
from surfaces.cli.wizard import components


@pytest.mark.parametrize("legacy_mode", ["aha", "focused"])
def test_local_defaults_maps_legacy_mode_to_quickstart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
    legacy_mode: str,
) -> None:
    monkeypatch.setattr(components, "get_store_path", lambda: tmp_path / "store.json")
    monkeypatch.setattr(
        components,
        "load_local_config",
        lambda _path: {"wizard": {"mode": legacy_mode}, "targets": {"local": {}}},
    )
    assert components.local_defaults()["wizard_mode"] == "quickstart"


def test_configure_selected_integrations_default_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[str] = []
    monkeypatch.setattr(
        configurators.console,
        "print",
        lambda msg, **_kwargs: printed.append(str(msg)),
    )
    monkeypatch.setattr(configurators, "choose", lambda *_args, **_kwargs: "skip")

    configurators._configure_selected_integrations()

    assert any("come back later" in line for line in printed)


def test_configure_selected_integrations_runs_one_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(configurators.console, "print", lambda *_a, **_k: None)
    monkeypatch.setattr(configurators, "step", lambda *_a, **_k: None)
    monkeypatch.setattr(configurators, "choose", lambda *_args, **_kwargs: "datadog")
    monkeypatch.setattr(
        configurators,
        "_configure_datadog",
        lambda: calls.append("datadog") or ("datadog", "/tmp/.env"),
    )

    configured, env_path = configurators._configure_selected_integrations()

    assert calls == ["datadog"]
    assert configured == ["datadog"]
    assert env_path == "/tmp/.env"
