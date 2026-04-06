from __future__ import annotations

import json

from app.cli.wizard.store import load_local_config, save_local_config


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
