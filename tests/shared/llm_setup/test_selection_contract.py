"""Selection transaction contracts, characterized before extracting the service."""

from __future__ import annotations

import io
from types import SimpleNamespace

from rich.console import Console

import surfaces.shared.llm_setup.model_presentation as switching
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


def test_invalid_model_does_not_commit_or_reset(monkeypatch):
    monkeypatch.setattr("config.account.account_llm_route", lambda: None)
    monkeypatch.setattr(
        "config.llm_auth.credentials.status",
        lambda _: SimpleNamespace(configured=True, stale=False),
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.selection._reset_runtime_llm_caches",
        lambda: (_ for _ in ()).throw(AssertionError("reset before validation")),
    )
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.env_sync.sync_provider_env",
        lambda **_: (_ for _ in ()).throw(AssertionError("write before validation")),
    )
    provider = PROVIDER_BY_VALUE["anthropic"]
    from dataclasses import replace

    monkeypatch.setitem(
        PROVIDER_BY_VALUE, "anthropic", replace(provider, allow_custom_models=False)
    )
    assert not switching.switch_llm_provider(
        "anthropic", Console(file=io.StringIO()), model="invalid-model"
    )
