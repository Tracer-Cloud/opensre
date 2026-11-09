"""Tests for Prefect integration classification (T-18: catalog wiring gap).

Prefect had a full client + tools + config model but was never registered
in ``integrations._catalog_impl``'s classifier map, so a Prefect entry in
``~/.opensre/integrations.json`` (the documented setup path, see
docs/integrations/workflows/prefect.mdx) was silently ignored.
"""

from __future__ import annotations

from integrations._catalog_impl import _classify_service_instance


class TestClassifyPrefect:
    def test_classify_prefect_cloud_with_api_key(self) -> None:
        flat_view, key = _classify_service_instance(
            "prefect",
            {
                "api_key": "pnu_test_key",
                "account_id": "acct-123",
                "workspace_id": "ws-456",
            },
            record_id="test-id",
        )

        assert key == "prefect"
        assert flat_view is not None
        assert flat_view.api_key == "pnu_test_key"
        assert flat_view.account_id == "acct-123"
        assert flat_view.workspace_id == "ws-456"
        assert flat_view.integration_id == "test-id"

    def test_classify_prefect_self_hosted_with_api_url_only(self) -> None:
        flat_view, key = _classify_service_instance(
            "prefect",
            {"api_url": "http://prefect-server:4200/api"},
            record_id="test-id",
        )

        assert key == "prefect"
        assert flat_view is not None
        assert flat_view.api_url == "http://prefect-server:4200/api"
        assert flat_view.api_key == ""

    def test_classify_prefect_rejects_empty_credentials(self) -> None:
        """Neither api_url nor api_key set — the config model's own Cloud
        default for api_url must not count as "configured"."""
        flat_view, key = _classify_service_instance("prefect", {}, record_id="test-id")

        assert flat_view is None
        assert key is None
