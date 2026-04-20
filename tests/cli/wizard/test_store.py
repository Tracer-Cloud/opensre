from __future__ import annotations

import json

from app.cli.wizard.store import (
    load_local_config,
    load_remote_ops_config,
    save_local_config,
    save_remote_ops_config,
)


def test_save_local_config_writes_versioned_payload(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    saved_path = save_local_config(
        wizard_mode="quickstart",
        provider="anthropic",
        model="claude-opus-4-5",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
        probes={
            "local": {"target": "local", "reachable": True, "detail": "ok"},
            "remote": {"target": "remote", "reachable": False, "detail": "down"},
        },
        path=store_path,
    )

    assert saved_path == store_path

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["version"] == 1
    assert payload["wizard"]["mode"] == "quickstart"
    assert payload["wizard"]["configured_target"] == "local"
    assert payload["targets"]["local"]["provider"] == "anthropic"
    assert payload["targets"]["local"]["model"] == "claude-opus-4-5"
    assert "api_key" not in payload["targets"]["local"]
    assert payload["probes"]["remote"]["reachable"] is False


def test_load_local_config_returns_independent_empty_payloads(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    first = load_local_config(store_path)
    first["targets"]["local"] = {"provider": "anthropic"}

    second = load_local_config(store_path)

    assert second["targets"] == {}


def test_remote_ops_config_round_trip(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    save_remote_ops_config(
        provider="railway",
        project="proj-a",
        service="svc-a",
        path=store_path,
    )

    loaded = load_remote_ops_config(store_path)
    assert loaded == {"provider": "railway", "project": "proj-a", "service": "svc-a"}


def test_remote_ops_config_clears_project_and_service(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    save_remote_ops_config(
        provider="railway",
        project="proj-b",
        service="svc-b",
        path=store_path,
    )
    save_remote_ops_config(
        provider="railway",
        project=None,
        service=None,
        path=store_path,
    )

    loaded = load_remote_ops_config(store_path)
    assert loaded == {"provider": "railway", "project": None, "service": None}
