"""Managed references survive task replacement without cloning query secrets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from config.constants.billing import ORGANIZATION_ID_ENV
from config.constants.paths import CONTEXT_ROOT_ENV
from config.constants.secrets import OPENSRE_DISABLE_KEYRING_ENV
from config.constants.signoz import SIGNOZ_API_KEY_ENV
from core.domain.alerts.triage.models import TriageSource
from core.domain.alerts.triage.storage import TriageStore
from integrations.signoz import connect_source, resolve_source_key
from integrations.store import replace_integrations


def prepare_managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TriageStore:
    monkeypatch.setenv(ORGANIZATION_ID_ENV, "org_triage_test")
    monkeypatch.setenv(CONTEXT_ROOT_ENV, str(tmp_path / "mount"))
    monkeypatch.setenv(OPENSRE_DISABLE_KEYRING_ENV, "1")
    monkeypatch.setattr("integrations.store.STORE_PATH", tmp_path / "hydrated.json")
    monkeypatch.setattr(
        "integrations.signoz.triage_setup.save_credential",
        lambda *_args: pytest.fail("Managed connection cloned a host-local secret"),
    )
    monkeypatch.setattr(
        "integrations.signoz.client.SigNozClient.query_traces",
        lambda _self, **_kwargs: {"available": True},
    )
    return TriageStore(tmp_path / "mount" / "alerts" / "triage.sqlite3")


def connect(store: TriageStore, **kwargs: str) -> TriageSource:
    return connect_source(
        store,
        name="Payments",
        query_url="https://signoz.example",
        api_key="managed-key",
        services=("payment",),
        ingress_url="https://gateway.example",
        **kwargs,
    )[0]


def managed_record(
    key: str = "managed-key", url: str = "https://signoz.example"
) -> list[dict[str, Any]]:
    return [
        {
            "id": "signoz-managed",
            "service": "signoz",
            "status": "active",
            "instances": [{"name": "prod", "credentials": {"url": url, "api_key": key}}],
        }
    ]


def test_managed_instance_reference_survives_rehydration_and_rejects_destination_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = prepare_managed(tmp_path, monkeypatch)
    replace_integrations(managed_record())
    source = connect(store)
    assert source.credential_kind == "integration"
    assert source.credential_ref == "signoz-managed"
    assert source.credential_instance == "prod"
    # A fresh gateway rehydrates credentials rather than retaining a host-local copy.
    replace_integrations(managed_record(key="rotated-managed-key"))
    recovered = TriageStore(store.path).source(source.id)
    assert resolve_source_key(recovered) == "rotated-managed-key"
    replace_integrations(managed_record(key="other-key", url="https://other.example"))
    assert resolve_source_key(recovered) == ""
    assert "managed-key" not in recovered.model_dump_json()


def test_environment_only_connection_retains_a_resolvable_managed_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = prepare_managed(tmp_path, monkeypatch)
    monkeypatch.setenv(SIGNOZ_API_KEY_ENV, "managed-key")
    source = connect(store, credential_env=SIGNOZ_API_KEY_ENV)
    assert source.credential_kind == "environment"
    assert source.credential_ref == SIGNOZ_API_KEY_ENV
    monkeypatch.setenv(SIGNOZ_API_KEY_ENV, "rotated-env-key")
    assert resolve_source_key(TriageStore(store.path).source(source.id)) == "rotated-env-key"
    monkeypatch.delenv(SIGNOZ_API_KEY_ENV)
    assert resolve_source_key(source) == ""


def test_deployment_rejects_a_key_without_a_managed_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = prepare_managed(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="managed SigNoz integration"):
        connect(store)
    assert store.sources() == []
