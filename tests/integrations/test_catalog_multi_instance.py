"""Tests for classify_integrations with multi-instance records."""

from __future__ import annotations

from app.integrations.catalog import classify_integrations


def _v1_grafana(endpoint: str = "https://x", api_key: str = "k") -> dict:
    return {
        "id": "store-grafana",
        "service": "grafana",
        "status": "active",
        "credentials": {"endpoint": endpoint, "api_key": api_key},
    }


def _v2_grafana_multi() -> dict:
    return {
        "id": "env-grafana",
        "service": "grafana",
        "status": "active",
        "instances": [
            {
                "name": "prod",
                "tags": {"env": "prod"},
                "credentials": {"endpoint": "https://prod", "api_key": "kp"},
            },
            {
                "name": "staging",
                "tags": {"env": "staging"},
                "credentials": {"endpoint": "https://staging", "api_key": "ks"},
            },
        ],
    }


def test_classify_single_v1_record_returns_flat_shape_unchanged() -> None:
    """Backward compat: v1 record with one instance produces the flat shape
    exactly as before (no _all_*_instances sibling when single default)."""
    resolved = classify_integrations([_v1_grafana()])
    assert "grafana" in resolved
    assert resolved["grafana"]["api_key"] == "k"
    assert resolved["grafana"]["endpoint"] == "https://x"
    # No sibling published for a single default-named instance
    assert "_all_grafana_instances" not in resolved


def test_classify_single_v2_record_returns_flat_shape() -> None:
    """v2 records pass through: flat shape is still the first instance's config."""
    v2_single = {
        "id": "g1",
        "service": "grafana",
        "status": "active",
        "instances": [
            {"name": "default", "tags": {}, "credentials": {"endpoint": "x", "api_key": "k"}}
        ],
    }
    resolved = classify_integrations([v2_single])
    assert resolved["grafana"]["api_key"] == "k"
    assert "_all_grafana_instances" not in resolved


def test_classify_multi_instance_record_exposes_sibling_all_grafana_instances() -> None:
    resolved = classify_integrations([_v2_grafana_multi()])
    # Default (first) view
    assert resolved["grafana"]["api_key"] == "kp"
    # Sibling with all instances
    assert "_all_grafana_instances" in resolved
    all_instances = resolved["_all_grafana_instances"]
    assert len(all_instances) == 2
    assert [i["name"] for i in all_instances] == ["prod", "staging"]


def test_all_grafana_instances_each_has_config_name_tags_integration_id() -> None:
    resolved = classify_integrations([_v2_grafana_multi()])
    first = resolved["_all_grafana_instances"][0]
    assert set(first.keys()) >= {"name", "tags", "config", "integration_id"}
    assert first["name"] == "prod"
    assert first["tags"] == {"env": "prod"}
    assert first["config"]["api_key"] == "kp"
    assert first["integration_id"] == "env-grafana"


def test_classify_two_records_same_service_both_preserved() -> None:
    """No silent last-wins: two records for the same service both get
    represented in the _all_*_instances sibling."""
    record_a = {
        "id": "a",
        "service": "grafana",
        "status": "active",
        "credentials": {"endpoint": "https://a", "api_key": "ka"},
    }
    record_b = {
        "id": "b",
        "service": "grafana",
        "status": "active",
        "credentials": {"endpoint": "https://b", "api_key": "kb"},
    }
    resolved = classify_integrations([record_a, record_b])
    # Flat shape is the FIRST (setdefault keeps it)
    assert resolved["grafana"]["api_key"] == "ka"
    # Both are in the sibling
    assert "_all_grafana_instances" in resolved
    ids = [i["integration_id"] for i in resolved["_all_grafana_instances"]]
    assert ids == ["a", "b"]


def test_classify_aws_with_role_arn_in_instance_credentials_works() -> None:
    """PR #527 bug #1 regression: AWS must read role_arn from instance.credentials,
    not from the record's top level."""
    v2_aws = {
        "id": "aws-1",
        "service": "aws",
        "status": "active",
        "instances": [
            {
                "name": "default",
                "tags": {},
                "credentials": {
                    "region": "us-east-1",
                    "role_arn": "arn:aws:iam::123456789012:role/opensre",
                    "external_id": "ext",
                },
            }
        ],
    }
    resolved = classify_integrations([v2_aws])
    assert "aws" in resolved
    assert resolved["aws"]["role_arn"] == "arn:aws:iam::123456789012:role/opensre"
    assert resolved["aws"]["external_id"] == "ext"
    assert resolved["aws"]["region"] == "us-east-1"


def test_classify_with_migrated_v1_aws_record_works() -> None:
    """Backward compat: passing a v1 AWS record with top-level role_arn still
    works because classify migrates on the fly."""
    v1_aws = {
        "id": "aws-1",
        "service": "aws",
        "status": "active",
        "role_arn": "arn:aws:iam::123:role/r",
        "external_id": "ext",
        "credentials": {"region": "us-east-1"},
    }
    resolved = classify_integrations([v1_aws])
    assert resolved["aws"]["role_arn"] == "arn:aws:iam::123:role/r"


def test_local_and_cloud_grafana_share_all_grafana_instances_bucket() -> None:
    """Regression for Devesh36 review: a local Grafana instance (classified
    as grafana_local) must be discoverable via the same _all_grafana_instances
    key that selectors look up under "grafana", so a hint like
    grafana_instance: "local" finds it."""
    v2_mixed = {
        "id": "env-grafana",
        "service": "grafana",
        "status": "active",
        "instances": [
            {
                "name": "local",
                "tags": {"env": "dev"},
                "credentials": {"endpoint": "http://localhost:3000", "api_key": "local"},
            },
            {
                "name": "prod",
                "tags": {"env": "prod"},
                "credentials": {"endpoint": "https://prod.grafana.net", "api_key": "kp"},
            },
        ],
    }
    resolved = classify_integrations([v2_mixed])
    # Both instances land in the same bucket under the "grafana" family key.
    assert "_all_grafana_instances" in resolved
    assert "_all_grafana_local_instances" not in resolved
    names = [i["name"] for i in resolved["_all_grafana_instances"]]
    assert set(names) == {"local", "prod"}


def test_classify_inactive_record_is_skipped() -> None:
    inactive = {
        "id": "g1",
        "service": "grafana",
        "status": "inactive",
        "credentials": {"endpoint": "x", "api_key": "k"},
    }
    resolved = classify_integrations([inactive])
    assert "grafana" not in resolved


def test_resolve_effective_integrations_propagates_single_non_default_instance() -> None:
    """Regression: when classify publishes _all_*_instances for a single
    non-default-named instance, resolve_effective_integrations must also
    propagate it so CLI/verify consumers see the instance metadata."""
    from app.integrations.catalog import resolve_effective_integrations

    single_prod = {
        "id": "env-grafana",
        "service": "grafana",
        "status": "active",
        "instances": [
            {
                "name": "prod",  # non-default name
                "tags": {"env": "prod"},
                "credentials": {"endpoint": "https://p", "api_key": "kp"},
            }
        ],
    }
    resolved = resolve_effective_integrations(
        store_integrations=[], env_integrations=[single_prod]
    )
    assert "instances" in resolved["grafana"]
    assert resolved["grafana"]["instances"][0]["name"] == "prod"


def test_resolve_effective_integrations_carries_instances_through_pydantic() -> None:
    """Regression: EffectiveIntegrations.model_validate must accept the
    {name, tags, config, integration_id} instance shape we build. Previously
    this used list[IntegrationInstance] which would reject the extra keys
    under StrictConfigModel's extra='forbid'."""
    from app.integrations.catalog import resolve_effective_integrations

    env_records = [_v2_grafana_multi()]
    resolved = resolve_effective_integrations(
        store_integrations=[], env_integrations=env_records
    )
    assert "grafana" in resolved
    assert "instances" in resolved["grafana"]
    all_inst = resolved["grafana"]["instances"]
    assert [i["name"] for i in all_inst] == ["prod", "staging"]
    assert "config" in all_inst[0]  # shape preserved through Pydantic
    assert "integration_id" in all_inst[0]
