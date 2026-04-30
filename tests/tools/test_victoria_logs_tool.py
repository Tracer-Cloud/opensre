"""Tests for VictoriaLogsTool.

Critically: the *executor-path contract test* — ``run(**extract_params(sources))`` —
catches the killer P1 from prior PRs (#663, #1060) where ``run()`` tried to read
credentials from ``kwargs["sources"]`` that the executor never passes. Any future
regression of that pattern will fail this test loudly.
"""

from __future__ import annotations

from unittest.mock import patch

from app.tools.VictoriaLogsTool import VictoriaLogsTool
from tests.tools.conftest import BaseToolContract


class TestVictoriaLogsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> VictoriaLogsTool:
        return VictoriaLogsTool()


class TestMetadata:
    def test_name(self) -> None:
        assert VictoriaLogsTool().name == "victoria_logs_query"

    def test_source(self) -> None:
        assert VictoriaLogsTool().source == "victoria_logs"

    def test_input_schema_declares_base_url(self) -> None:
        schema = VictoriaLogsTool().input_schema
        assert "base_url" in schema["properties"]
        assert "base_url" in schema["required"]

    def test_input_schema_declares_query_with_default(self) -> None:
        # ``query`` is intentionally NOT in ``required``: detect_sources doesn't
        # yet propagate alert-derived queries, so extract_params always supplies
        # the wildcard default. Marking ``query`` required would lie about the
        # tool's actual contract.
        schema = VictoriaLogsTool().input_schema
        assert "query" in schema["properties"]
        assert schema["properties"]["query"].get("default") == "*"
        assert "query" not in schema["required"]


class TestIsAvailable:
    def test_true_when_base_url_present(self) -> None:
        sources = {"victoria_logs": {"base_url": "http://vmlogs:9428"}}
        assert VictoriaLogsTool().is_available(sources) is True

    def test_false_when_no_victoria_logs_in_sources(self) -> None:
        assert VictoriaLogsTool().is_available({}) is False

    def test_false_when_base_url_empty(self) -> None:
        sources = {"victoria_logs": {"base_url": ""}}
        assert VictoriaLogsTool().is_available(sources) is False


class TestExtractParams:
    """``extract_params`` must surface every kwarg that ``run`` declares.

    The investigation executor calls ``tool.run(**tool.extract_params(sources))``
    with no ``sources`` kwarg. If extract_params does not return base_url and
    tenant_id, run() will receive defaults / errors and the tool will be
    permanently broken via the executor — exactly what happened in PRs #663
    and #1060.
    """

    def test_returns_base_url(self) -> None:
        sources = {
            "victoria_logs": {"base_url": "http://vmlogs:9428"},
        }
        params = VictoriaLogsTool().extract_params(sources)
        assert params["base_url"] == "http://vmlogs:9428"

    def test_returns_tenant_id(self) -> None:
        sources = {
            "victoria_logs": {"base_url": "http://vmlogs:9428", "tenant_id": "acme"},
        }
        params = VictoriaLogsTool().extract_params(sources)
        assert params["tenant_id"] == "acme"

    def test_returns_default_query_when_unset(self) -> None:
        sources = {"victoria_logs": {"base_url": "http://vmlogs:9428"}}
        params = VictoriaLogsTool().extract_params(sources)
        assert params["query"] == "*"

    def test_returns_defaults_for_limit_and_start(self) -> None:
        sources = {"victoria_logs": {"base_url": "http://vmlogs:9428"}}
        params = VictoriaLogsTool().extract_params(sources)
        assert params["limit"] == 50
        assert params["start"] == "-1h"


class TestExecutorPathContract:
    """The contract test that prior PRs missed.

    Calling ``tool.run(**tool.extract_params(sources))`` mirrors
    ``execute_actions.py``. If extract_params/run drift apart, this test fails —
    surfacing the regression at unit-test time rather than at runtime in a real
    investigation.
    """

    def test_run_via_extract_params_succeeds(self) -> None:
        sources = {
            "victoria_logs": {
                "base_url": "http://vmlogs:9428",
                "query": "level:error",
                "limit": 10,
                "start": "-30m",
            }
        }
        tool = VictoriaLogsTool()
        params = tool.extract_params(sources)

        with patch("app.tools.VictoriaLogsTool.make_victoria_logs_client") as mock_factory:
            mock_client = mock_factory.return_value
            mock_client.__enter__.return_value = mock_client
            mock_client.query_logs.return_value = {
                "success": True,
                "rows": [{"_msg": "boom"}],
                "total": 1,
            }

            result = tool.run(**params)

        assert result["available"] is True
        assert result["source"] == "victoria_logs"
        assert result["total"] == 1
        assert result["rows"] == [{"_msg": "boom"}]
        # Verify credentials propagated correctly through extract_params -> run -> factory.
        mock_factory.assert_called_once_with("http://vmlogs:9428", tenant_id=None)

    def test_run_via_extract_params_with_tenant_id(self) -> None:
        sources = {
            "victoria_logs": {
                "base_url": "http://vmlogs:9428",
                "tenant_id": "team-a",
                "query": "*",
            }
        }
        tool = VictoriaLogsTool()
        params = tool.extract_params(sources)

        with patch("app.tools.VictoriaLogsTool.make_victoria_logs_client") as mock_factory:
            mock_client = mock_factory.return_value
            mock_client.__enter__.return_value = mock_client
            mock_client.query_logs.return_value = {"success": True, "rows": [], "total": 0}

            tool.run(**params)

        mock_factory.assert_called_once_with("http://vmlogs:9428", tenant_id="team-a")


class TestRun:
    def test_unavailable_when_base_url_empty(self) -> None:
        result = VictoriaLogsTool().run(base_url="", query="*")
        assert result["available"] is False
        assert "base_url" in result["error"]

    def test_propagates_query_failure(self) -> None:
        with patch("app.tools.VictoriaLogsTool.make_victoria_logs_client") as mock_factory:
            mock_client = mock_factory.return_value
            mock_client.__enter__.return_value = mock_client
            mock_client.query_logs.return_value = {
                "success": False,
                "error": "HTTP 500: kaboom",
            }
            result = VictoriaLogsTool().run(
                base_url="http://vmlogs:9428",
                query="*",
            )

        assert result["available"] is False
        assert "kaboom" in result["error"]
        assert result["rows"] == []
