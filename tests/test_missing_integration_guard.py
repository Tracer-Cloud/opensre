from __future__ import annotations

import pytest

from app.integrations.required_integrations import (
    MissingIntegrationError,
    required_integration_for_source,
    validate_required_integrations,
)
from app.nodes.resolve_integrations.node import node_resolve_integrations
from app.pipeline.runners import run_investigation


def test_required_integration_for_source_with_explicit_source_alias() -> None:
    assert required_integration_for_source({"alert_source": "grafana_local"}) == "grafana"


def test_required_integration_for_source_detects_grafana_from_generator_url() -> None:
    assert required_integration_for_source(
        {
            "generatorURL": "https://grafana.example.com/d/abcd1234/alert",
        }
    ) == "grafana"


def test_required_integration_for_source_detects_aws_from_cloudwatch_metadata() -> None:
    assert required_integration_for_source(
        {"cloudwatch_log_group": "/aws/lambda/my-func"}
    ) == "aws"


def test_required_integration_for_source_does_not_infer_aws_from_generic_aws_metadata() -> None:
    assert required_integration_for_source(
        {"db_instance_identifier": "my-db", "aws_account_id": "123456789012"}
    ) is None


def test_validate_required_integrations_raises_when_required_integration_missing() -> None:
    with pytest.raises(MissingIntegrationError, match="aws integration"):
        validate_required_integrations(
            {"cloudwatch_log_group": "/aws/lambda/my-func"},
            {},
        )


def test_run_investigation_validates_pre_injected_resolved_integrations(monkeypatch) -> None:
    called: dict[str, bool] = {}

    def fake_invoke(initial: dict[str, object]) -> dict[str, object]:
        called["invoked"] = True
        return {"slack_message": "ok"}

    monkeypatch.setattr("app.pipeline.graph.graph.invoke", fake_invoke)

    result = run_investigation(
        "alert",
        "pipeline",
        "warning",
        raw_alert={"alert_source": "datadog"},
        resolved_integrations={"datadog": {"api_key": "x", "app_key": "y"}},
    )

    assert called["invoked"] is True
    assert result["slack_message"] == "ok"


def test_run_investigation_rejects_invalid_pre_injected_resolved_integrations(monkeypatch) -> None:
    with pytest.raises(MissingIntegrationError, match="datadog integration"):
        run_investigation(
            "alert",
            "pipeline",
            "warning",
            raw_alert={"alert_source": "datadog"},
            resolved_integrations={"grafana": {"endpoint": "https://grafana.example.com"}},
        )


def test_run_investigation_allows_mock_backend_injection(monkeypatch) -> None:
    called: dict[str, bool] = {}

    def fake_invoke(initial: dict[str, object]) -> dict[str, object]:
        called["invoked"] = True
        return {"success": True}

    monkeypatch.setattr("app.pipeline.graph.graph.invoke", fake_invoke)

    class DummyBackend:
        pass

    result = run_investigation(
        "alert",
        "pipeline",
        "warning",
        raw_alert={"cloudwatch_log_group": "/aws/lambda/my-func"},
        resolved_integrations={
            "grafana": {"endpoint": "", "api_key": "", "_backend": DummyBackend()},
        },
    )

    assert called["invoked"] is True
    assert result["success"] is True


def test_node_resolve_integrations_fails_when_required_integration_missing(monkeypatch) -> None:
    class DummyTracker:
        def start(self, *args: object, **kwargs: object) -> None:
            pass

        def complete(self, *args: object, **kwargs: object) -> None:
            pass

    monkeypatch.setattr("app.nodes.resolve_integrations.node.get_tracker", lambda: DummyTracker())
    monkeypatch.setattr("app.integrations.store.load_integrations", lambda: [])
    monkeypatch.setattr("app.nodes.resolve_integrations.node._load_env_integrations", lambda: [])

    with pytest.raises(MissingIntegrationError, match="datadog integration"):
        node_resolve_integrations({"raw_alert": {"alert_source": "datadog"}})
