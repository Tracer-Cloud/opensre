"""Catalog wiring for the Temporal integration.

Regression coverage for the classifier path: a stored/remote Temporal record
must be flattened into a typed config whose fields the Temporal tools can read.
Before the classifier existed, a missing ``_CLASSIFIERS["temporal"]`` entry meant
records fell through the generic passthrough, leaving ``base_url`` nested under
``credentials`` so ``is_available``/``extract_params`` silently saw nothing.
"""

from __future__ import annotations

from core.tool import availability_view
from integrations.catalog import classify_integrations
from integrations.temporal import classify
from integrations.temporal.client import TemporalConfig
from integrations.temporal.tools import TemporalWorkflowsTool


class TestClassify:
    def test_returns_typed_config_with_required_fields(self) -> None:
        cfg, key = classify(
            {"base_url": "http://localhost:7243", "namespace": "production"},
            record_id="rec-1",
        )
        assert key == "temporal"
        assert isinstance(cfg, TemporalConfig)
        assert cfg.base_url == "http://localhost:7243"
        assert cfg.namespace == "production"
        assert cfg.integration_id == "rec-1"

    def test_defaults_namespace_when_absent(self) -> None:
        cfg, key = classify({"base_url": "http://localhost:7243"}, record_id="rec-1")
        assert key == "temporal"
        assert cfg is not None
        assert cfg.namespace == "default"

    def test_skips_record_without_base_url(self) -> None:
        cfg, key = classify({"namespace": "production"}, record_id="rec-1")
        assert cfg is None
        assert key is None


class TestCatalogEndToEnd:
    """The real wiring tools depend on: store record -> resolved -> tool params."""

    def test_stored_record_reaches_tool_flattened(self) -> None:
        resolved = classify_integrations(
            [
                {
                    "service": "temporal",
                    "id": "tmprl-1",
                    "status": "active",
                    "credentials": {
                        "base_url": "http://localhost:7243",
                        "namespace": "production",
                        "api_key": "",
                    },
                }
            ]
        )
        view = availability_view(resolved)
        tool = TemporalWorkflowsTool()

        assert tool.is_available(view) is True
        params = tool.extract_params(view)
        assert params["base_url"] == "http://localhost:7243"
        assert params["namespace"] == "production"

    def test_unconfigured_record_is_not_advertised(self) -> None:
        resolved = classify_integrations(
            [
                {
                    "service": "temporal",
                    "id": "tmprl-1",
                    "status": "active",
                    "credentials": {"namespace": "default"},
                }
            ]
        )
        assert resolved.get("temporal") is None
        assert TemporalWorkflowsTool().is_available(availability_view(resolved)) is False
