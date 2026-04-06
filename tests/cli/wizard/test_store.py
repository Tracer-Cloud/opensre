from __future__ import annotations

import json

import pytest

from app.cli.wizard.store import (
    delete_named_remote,
    load_active_remote_name,
    load_local_config,
    load_named_remotes,
    load_remote_url,
    save_local_config,
    save_named_remote,
    save_remote_url,
    set_active_remote,
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


# ---------------------------------------------------------------------------
# Named remotes
# ---------------------------------------------------------------------------


class TestNamedRemotes:
    def test_save_named_remote_creates_entry(self, tmp_path) -> None:
        store_path = tmp_path / "opensre.json"

        save_named_remote("ec2", "http://1.2.3.4:8080", source="deploy", path=store_path)

        remotes = load_named_remotes(store_path)
        assert remotes == {"ec2": "http://1.2.3.4:8080"}

    def test_save_named_remote_with_set_active(self, tmp_path) -> None:
        store_path = tmp_path / "opensre.json"

        save_named_remote(
            "ec2", "http://1.2.3.4:8080", set_active=True, source="deploy", path=store_path
        )

        assert load_remote_url(store_path) == "http://1.2.3.4:8080"
        assert load_active_remote_name(store_path) == "ec2"

    def test_multiple_remotes(self, tmp_path) -> None:
        store_path = tmp_path / "opensre.json"

        save_named_remote("ec2", "http://1.2.3.4:8080", source="deploy", path=store_path)
        save_named_remote("local", "http://localhost:8080", source="manual", path=store_path)

        remotes = load_named_remotes(store_path)
        assert remotes == {
            "ec2": "http://1.2.3.4:8080",
            "local": "http://localhost:8080",
        }

    def test_set_active_remote_switches_url(self, tmp_path) -> None:
        store_path = tmp_path / "opensre.json"

        save_named_remote(
            "ec2", "http://1.2.3.4:8080", set_active=True, source="deploy", path=store_path
        )
        save_named_remote("local", "http://localhost:8080", source="manual", path=store_path)

        url = set_active_remote("local", store_path)

        assert url == "http://localhost:8080"
        assert load_remote_url(store_path) == "http://localhost:8080"
        assert load_active_remote_name(store_path) == "local"

    def test_set_active_remote_raises_for_unknown_name(self, tmp_path) -> None:
        store_path = tmp_path / "opensre.json"

        with pytest.raises(KeyError, match="No remote named 'missing'"):
            set_active_remote("missing", store_path)

    def test_delete_named_remote(self, tmp_path) -> None:
        store_path = tmp_path / "opensre.json"

        save_named_remote("ec2", "http://1.2.3.4:8080", set_active=True, path=store_path)
        save_named_remote("local", "http://localhost:8080", path=store_path)

        delete_named_remote("ec2", store_path)

        assert load_named_remotes(store_path) == {"local": "http://localhost:8080"}
        assert load_active_remote_name(store_path) is None

    def test_deploy_overwrites_existing_ec2_remote(self, tmp_path) -> None:
        store_path = tmp_path / "opensre.json"

        save_named_remote("ec2", "http://old-ip:8080", set_active=True, path=store_path)
        save_named_remote("ec2", "http://new-ip:8080", set_active=True, path=store_path)

        remotes = load_named_remotes(store_path)
        assert remotes == {"ec2": "http://new-ip:8080"}
        assert load_remote_url(store_path) == "http://new-ip:8080"

    def test_save_remote_url_still_works_standalone(self, tmp_path) -> None:
        """Backward compat: save_remote_url still sets the active URL."""
        store_path = tmp_path / "opensre.json"

        save_remote_url("http://legacy:8080", store_path)

        assert load_remote_url(store_path) == "http://legacy:8080"

    def test_load_named_remotes_empty_on_fresh_store(self, tmp_path) -> None:
        store_path = tmp_path / "opensre.json"

        assert load_named_remotes(store_path) == {}
        assert load_active_remote_name(store_path) is None
