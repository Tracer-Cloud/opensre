from __future__ import annotations

import json

from config.setup_store import (
    delete_named_remote,
    load_local_config,
    load_named_remotes,
    load_remote_ops_config,
    load_remote_url,
    save_local_config,
    save_named_remote,
    save_remote_ops_config,
    update_local_llm_selection,
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


def test_update_local_llm_selection_merges_without_resetting_other_fields(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"
    save_local_config(
        wizard_mode="quickstart",
        provider="anthropic",
        model="claude-haiku",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_MODEL",
        probes={"local": {"target": "local", "reachable": True, "detail": "ok"}},
        path=store_path,
    )

    update_local_llm_selection(
        provider="openai",
        model="gpt-5-mini",
        api_key_env="OPENAI_API_KEY",
        model_env="OPENAI_REASONING_MODEL",
        path=store_path,
    )

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    assert payload["wizard"]["mode"] == "quickstart"
    assert payload["probes"]["local"]["reachable"] is True
    assert payload["targets"]["local"]["provider"] == "openai"
    assert payload["targets"]["local"]["model"] == "gpt-5-mini"
    assert payload["targets"]["local"]["api_key_env"] == "OPENAI_API_KEY"
    assert payload["targets"]["local"]["model_env"] == "OPENAI_REASONING_MODEL"
    assert "updated_at" in payload["targets"]["local"]


def test_update_local_llm_selection_clears_api_key_env_for_cli_provider(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"
    save_local_config(
        wizard_mode="quickstart",
        provider="anthropic",
        model="claude-haiku",
        api_key_env="ANTHROPIC_API_KEY",
        model_env="ANTHROPIC_REASONING_MODEL",
        probes={"local": {"target": "local", "reachable": True, "detail": "ok"}},
        path=store_path,
    )

    update_local_llm_selection(
        provider="claude-code",
        model="",
        api_key_env="",
        model_env="CLAUDE_CODE_MODEL",
        path=store_path,
    )

    payload = json.loads(store_path.read_text(encoding="utf-8"))
    local = payload["targets"]["local"]
    assert local["provider"] == "claude-code"
    assert local["api_key_env"] == ""
    assert local["model_env"] == "CLAUDE_CODE_MODEL"


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


def test_save_named_remote_persists_url(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    save_named_remote("ec2", "http://1.2.3.4:8080", set_active=True, source="ec2", path=store_path)

    assert load_remote_url(store_path) == "http://1.2.3.4:8080"
    assert load_named_remotes(store_path) == {"ec2": "http://1.2.3.4:8080"}


def test_delete_named_remote_removes_entry_and_clears_active_url(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"

    save_named_remote("ec2", "http://1.2.3.4:8080", set_active=True, source="ec2", path=store_path)
    assert load_remote_url(store_path) == "http://1.2.3.4:8080"

    delete_named_remote("ec2", store_path)

    assert load_remote_url(store_path) is None
    assert load_named_remotes(store_path) == {}


def test_delete_named_remote_does_not_clear_url_when_different_remote_is_active(
    tmp_path,
) -> None:
    store_path = tmp_path / "opensre.json"

    save_named_remote("ec2", "http://1.2.3.4:8080", set_active=False, source="ec2", path=store_path)
    save_named_remote(
        "railway", "http://railway.app", set_active=True, source="railway", path=store_path
    )

    delete_named_remote("ec2", store_path)

    assert load_remote_url(store_path) == "http://railway.app"
    assert load_named_remotes(store_path) == {"railway": "http://railway.app"}


def test_delete_named_remote_is_noop_when_name_missing(tmp_path) -> None:
    store_path = tmp_path / "opensre.json"
    save_named_remote("ec2", "http://1.2.3.4:8080", set_active=True, source="ec2", path=store_path)

    delete_named_remote("nonexistent", store_path)

    assert load_remote_url(store_path) == "http://1.2.3.4:8080"


def test_deploy_destroy_lifecycle_updates_opensre_json(tmp_path) -> None:
    """Simulate deploy_remote + destroy_remote config-persistence without live AWS."""
    store_path = tmp_path / "opensre.json"
    stack_name = "opensre-ec2-remote"
    remote_url = "http://54.0.0.1:8080"

    # --- deploy ---
    save_named_remote(stack_name, remote_url, set_active=True, source="ec2", path=store_path)

    assert load_remote_url(store_path) == remote_url
    remotes = load_named_remotes(store_path)
    assert remotes[stack_name] == remote_url

    raw = json.loads(store_path.read_text())
    assert raw["remote"]["active_name"] == stack_name

    # --- destroy ---
    delete_named_remote(stack_name, store_path)

    assert load_remote_url(store_path) is None
    assert load_named_remotes(store_path) == {}


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
