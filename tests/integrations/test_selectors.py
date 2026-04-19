"""Tests for app/integrations/selectors.py."""

from __future__ import annotations

from app.integrations.selectors import (
    get_default_instance,
    get_instance_by_name,
    get_instances,
    get_instances_by_tag,
    select_instance,
)


def _resolved_single() -> dict:
    return {
        "grafana": {"endpoint": "https://x", "api_key": "k", "integration_id": "g1"},
    }


def _resolved_multi() -> dict:
    return {
        "grafana": {"endpoint": "https://prod", "api_key": "kp", "integration_id": "env-grafana"},
        "_all_grafana_instances": [
            {
                "name": "prod",
                "tags": {"env": "prod", "region": "us-east-1"},
                "config": {"endpoint": "https://prod", "api_key": "kp"},
                "integration_id": "env-grafana",
            },
            {
                "name": "staging",
                "tags": {"env": "staging", "region": "us-west-2"},
                "config": {"endpoint": "https://staging", "api_key": "ks"},
                "integration_id": "env-grafana",
            },
        ],
    }


def test_get_instances_returns_empty_when_service_absent() -> None:
    assert get_instances({}, "grafana") == []
    assert get_instances(None, "grafana") == []


def test_get_instances_returns_list_when_present() -> None:
    instances = get_instances(_resolved_multi(), "grafana")
    assert [i["name"] for i in instances] == ["prod", "staging"]


def test_get_instances_synthesizes_single_entry_when_only_flat_shape() -> None:
    instances = get_instances(_resolved_single(), "grafana")
    assert len(instances) == 1
    assert instances[0]["name"] == "default"
    assert instances[0]["config"]["api_key"] == "k"


def test_get_default_instance_returns_flat_shape() -> None:
    assert get_default_instance(_resolved_multi(), "grafana") == {
        "endpoint": "https://prod",
        "api_key": "kp",
        "integration_id": "env-grafana",
    }


def test_get_default_instance_none_when_service_absent() -> None:
    assert get_default_instance({}, "grafana") is None


def test_get_instance_by_name_returns_matching_config() -> None:
    config = get_instance_by_name(_resolved_multi(), "grafana", "staging")
    assert config is not None
    assert config["endpoint"] == "https://staging"


def test_get_instance_by_name_returns_none_when_unknown() -> None:
    assert get_instance_by_name(_resolved_multi(), "grafana", "unknown") is None


def test_get_instance_by_name_empty_name_returns_none() -> None:
    assert get_instance_by_name(_resolved_multi(), "grafana", "") is None


def test_get_instances_by_tag_matches_single_kv() -> None:
    results = get_instances_by_tag(_resolved_multi(), "grafana", "env", "staging")
    assert len(results) == 1
    assert results[0]["endpoint"] == "https://staging"


def test_get_instances_by_tag_returns_empty_when_no_match() -> None:
    assert get_instances_by_tag(_resolved_multi(), "grafana", "env", "qa") == []


def test_select_instance_prefers_name_over_tags_when_both_given() -> None:
    # name="prod" matches; tags={"env":"staging"} would not — name wins
    config = select_instance(_resolved_multi(), "grafana", name="prod", tags={"env": "staging"})
    assert config is not None
    assert config["endpoint"] == "https://prod"


def test_select_instance_with_no_filters_returns_default() -> None:
    config = select_instance(_resolved_multi(), "grafana")
    assert config is not None
    assert config["endpoint"] == "https://prod"  # first/default


def test_select_instance_by_tags_only() -> None:
    config = select_instance(_resolved_multi(), "grafana", tags={"env": "staging"})
    assert config is not None
    assert config["endpoint"] == "https://staging"


def test_select_instance_by_name_no_match_returns_none() -> None:
    # Per docstring: name-based lookup doesn't silently fall back
    assert select_instance(_resolved_multi(), "grafana", name="nope") is None
