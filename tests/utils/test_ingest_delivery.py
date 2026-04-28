"""Tests for ``app/utils/ingest_delivery.py``.

Covers payload construction (``_normalize_severity``, ``_resolve_source``,
``_resolve_thread_id``, ``build_ingest_payload``) and the ``send_ingest``
delivery wrapper.

``send_ingest`` tests stub ``app.utils.ingest_delivery.httpx.post`` and
``app.utils.ingest_delivery.get_tracer_base_url`` so the real network is
never touched and the URL resolution is deterministic.
"""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

# Prime the heavy import chain (app.state → app.nodes → app.utils.ingest_delivery)
# before grabbing a direct handle to ingest_delivery, otherwise running this
# file in isolation hits the partial-init circular import.
importlib.import_module("app.nodes")
from app.utils import ingest_delivery


def _mock_response(
    status_code: int = 200,
    json_body: Any = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.text = text
    resp.json.return_value = json_body if json_body is not None else {}
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "boom", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# _normalize_severity
# ---------------------------------------------------------------------------


class TestNormalizeSeverity:
    @pytest.mark.parametrize("level", ["critical", "high", "warning", "info"])
    def test_known_lowercase_passthrough(self, level: str) -> None:
        assert ingest_delivery._normalize_severity(level) == level

    @pytest.mark.parametrize("level", ["CRITICAL", "High", "Warning", "INFO"])
    def test_known_mixed_case_lowercased(self, level: str) -> None:
        assert ingest_delivery._normalize_severity(level) == level.lower()

    def test_none_falls_back_to_info(self) -> None:
        assert ingest_delivery._normalize_severity(None) == "info"

    def test_empty_string_falls_back_to_info(self) -> None:
        assert ingest_delivery._normalize_severity("") == "info"

    def test_unknown_severity_falls_back_to_info(self) -> None:
        assert ingest_delivery._normalize_severity("p0") == "info"


# ---------------------------------------------------------------------------
# _resolve_source
# ---------------------------------------------------------------------------


class TestResolveSource:
    def test_uses_raw_alert_source_when_present(self) -> None:
        state: dict[str, Any] = {"raw_alert": {"source": "datadog"}}
        assert ingest_delivery._resolve_source(state) == "datadog"

    def test_falls_back_to_slack_when_team_id(self) -> None:
        state: dict[str, Any] = {
            "raw_alert": {},
            "slack_context": {"team_id": "T123"},
        }
        assert ingest_delivery._resolve_source(state) == "slack"

    def test_defaults_to_tracer(self) -> None:
        assert ingest_delivery._resolve_source({}) == "tracer"

    def test_raw_alert_non_dict_falls_through(self) -> None:
        state: dict[str, Any] = {"raw_alert": "not-a-dict", "slack_context": {}}
        assert ingest_delivery._resolve_source(state) == "tracer"

    def test_raw_alert_source_takes_precedence_over_slack(self) -> None:
        state: dict[str, Any] = {
            "raw_alert": {"source": "grafana"},
            "slack_context": {"team_id": "T123"},
        }
        assert ingest_delivery._resolve_source(state) == "grafana"


# ---------------------------------------------------------------------------
# _resolve_thread_id
# ---------------------------------------------------------------------------


class TestResolveThreadId:
    def test_explicit_thread_id_wins(self) -> None:
        state: dict[str, Any] = {
            "thread_id": "t-1",
            "slack_context": {"thread_ts": "1.0", "ts": "2.0"},
            "run_id": "r-9",
        }
        assert ingest_delivery._resolve_thread_id(state) == "t-1"

    def test_falls_back_to_slack_thread_ts(self) -> None:
        state: dict[str, Any] = {"slack_context": {"thread_ts": "1.0", "ts": "2.0"}}
        assert ingest_delivery._resolve_thread_id(state) == "1.0"

    def test_falls_back_to_slack_ts_when_no_thread_ts(self) -> None:
        state: dict[str, Any] = {"slack_context": {"ts": "2.0"}}
        assert ingest_delivery._resolve_thread_id(state) == "2.0"

    def test_falls_back_to_run_id(self) -> None:
        state: dict[str, Any] = {"slack_context": {}, "run_id": "r-9"}
        assert ingest_delivery._resolve_thread_id(state) == "r-9"

    def test_returns_empty_string_when_nothing_set(self) -> None:
        assert ingest_delivery._resolve_thread_id({}) == ""


# ---------------------------------------------------------------------------
# build_ingest_payload
# ---------------------------------------------------------------------------


class TestBuildIngestPayload:
    def test_full_payload_shape(self) -> None:
        state: dict[str, Any] = {
            "org_id": "org-1",
            "alert_name": "checkout-api 5xx",
            "pipeline_name": "p-1",
            "severity": "Critical",
            "summary": "rate of 5xx spiked",
            "raw_alert": {"source": "datadog", "fingerprint": "fp-1", "fired_at": "ts"},
            "root_cause": "rds disk full",
            "validity_score": 87,
            "planned_actions": [{"tool": "x"}],
            "problem_md": "## detail",
            "investigation_recommendations": ["bump disk"],
            "thread_id": "t-1",
            "run_id": "r-1",
        }
        payload = ingest_delivery.build_ingest_payload(state)
        out = payload["investigation_output"]
        meta = payload["metadata"]
        assert out["org_id"] == "org-1"
        assert out["alert_name"] == "checkout-api 5xx"
        assert out["pipeline_name"] == "p-1"
        assert out["severity"] == "critical"
        assert out["summary"] == "rate of 5xx spiked"
        assert out["raw_alert"]["fingerprint"] == "fp-1"
        assert out["root_cause"] == "rds disk full"
        assert out["confidence"] == 87
        assert out["validity_score"] == 87
        assert out["planned_actions"] == [{"tool": "x"}]
        assert out["problem_md"] == "## detail"
        assert out["investigation_recommendations"] == ["bump disk"]
        assert "problem_report" not in out
        assert meta["source"] == "datadog"
        assert meta["investigation_type"] == "auto"
        assert meta["connection_type"] == "platform"
        assert meta["alert_fired_at"] == "ts"
        assert meta["thread_id"] == "t-1"
        assert meta["run_id"] == "r-1"

    def test_summary_falls_back_to_problem_md(self) -> None:
        state: dict[str, Any] = {"problem_md": "## detail", "alert_name": "a"}
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["summary"] == "## detail"

    def test_summary_falls_back_to_root_cause(self) -> None:
        state: dict[str, Any] = {"root_cause": "rds full", "alert_name": "a"}
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["summary"] == "rds full"

    def test_summary_falls_back_to_alert_name(self) -> None:
        state: dict[str, Any] = {"alert_name": "checkout-api 5xx"}
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["summary"] == "checkout-api 5xx"

    def test_fingerprint_backfilled_from_thread_id(self) -> None:
        state: dict[str, Any] = {"raw_alert": {}, "thread_id": "t-1"}
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["raw_alert"]["fingerprint"] == "t-1"

    def test_fingerprint_backfilled_from_run_id_when_no_thread_id(self) -> None:
        state: dict[str, Any] = {"raw_alert": {}, "run_id": "r-9"}
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["raw_alert"]["fingerprint"] == "r-9"

    def test_fingerprint_backfilled_from_alert_id(self) -> None:
        state: dict[str, Any] = {"raw_alert": {"alert_id": "a-1"}}
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["raw_alert"]["fingerprint"] == "a-1"

    def test_fingerprint_preserved_when_already_set(self) -> None:
        state: dict[str, Any] = {
            "raw_alert": {"fingerprint": "existing"},
            "thread_id": "t-1",
        }
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["raw_alert"]["fingerprint"] == "existing"

    def test_fingerprint_left_unset_when_no_candidates(self) -> None:
        state: dict[str, Any] = {"raw_alert": {}}
        payload = ingest_delivery.build_ingest_payload(state)
        assert "fingerprint" not in payload["investigation_output"]["raw_alert"]

    def test_problem_report_attached_when_present(self) -> None:
        state: dict[str, Any] = {"problem_report": {"sections": ["a", "b"]}}
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["problem_report"] == {"sections": ["a", "b"]}

    def test_raw_alert_non_dict_replaced_by_empty(self) -> None:
        state: dict[str, Any] = {"raw_alert": "not-a-dict"}
        payload = ingest_delivery.build_ingest_payload(state)
        assert payload["investigation_output"]["raw_alert"] == {}
        assert payload["metadata"]["alert_fired_at"] is None

    def test_defaults_when_state_empty(self) -> None:
        payload = ingest_delivery.build_ingest_payload({})
        out = payload["investigation_output"]
        meta = payload["metadata"]
        assert out["severity"] == "info"
        assert out["raw_alert"] == {}
        assert out["root_cause"] == ""
        assert out["confidence"] == 0
        assert out["validity_score"] == 0
        assert out["planned_actions"] == []
        assert out["problem_md"] == ""
        assert out["investigation_recommendations"] == []
        assert meta["source"] == "tracer"
        assert meta["thread_id"] == ""
        assert meta["run_id"] == ""


# ---------------------------------------------------------------------------
# send_ingest
# ---------------------------------------------------------------------------


class TestSendIngest:
    def test_returns_none_when_token_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TRACER_INGEST_TOKEN", raising=False)
        monkeypatch.setenv("TRACER_API_URL", "https://api.example.com")
        monkeypatch.setattr(
            "app.utils.ingest_delivery.get_tracer_base_url",
            lambda: "https://api.example.com",
        )
        called: dict[str, Any] = {}

        def _capture(*_a: Any, **_kw: Any) -> Any:  # pragma: no cover - guard
            called["hit"] = True
            return _mock_response()

        monkeypatch.setattr("app.utils.ingest_delivery.httpx.post", _capture)
        assert ingest_delivery.send_ingest({"thread_id": "t"}) is None
        assert called == {}

    def test_returns_none_when_thread_id_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_INGEST_TOKEN", "tok")
        monkeypatch.setenv("TRACER_API_URL", "https://api.example.com")
        called: dict[str, Any] = {}

        def _capture(*_a: Any, **_kw: Any) -> Any:  # pragma: no cover - guard
            called["hit"] = True
            return _mock_response()

        monkeypatch.setattr("app.utils.ingest_delivery.httpx.post", _capture)
        assert ingest_delivery.send_ingest({"raw_alert": {}}) is None
        assert called == {}

    def test_happy_path_returns_investigation_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_INGEST_TOKEN", "tok")
        monkeypatch.setenv("TRACER_API_URL", "https://api.example.com")
        captured: dict[str, Any] = {}

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            captured["url"] = url
            captured.update(kwargs)
            return _mock_response(json_body={"data": {"investigation_id": "inv-9"}})

        monkeypatch.setattr("app.utils.ingest_delivery.httpx.post", _capture)
        result = ingest_delivery.send_ingest({"thread_id": "t-1"})
        assert result == "inv-9"
        assert captured["url"] == "https://api.example.com/api/investigations/ingest"
        assert captured["headers"] == {"Authorization": "Bearer tok"}
        assert captured["timeout"] == 10.0
        assert captured["json"]["metadata"]["thread_id"] == "t-1"

    def test_uses_get_tracer_base_url_when_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRACER_INGEST_TOKEN", "tok")
        monkeypatch.delenv("TRACER_API_URL", raising=False)
        monkeypatch.setattr(
            "app.utils.ingest_delivery.get_tracer_base_url",
            lambda: "https://fallback.example.com/",
        )
        captured: dict[str, Any] = {}

        def _capture(url: str, **kwargs: Any) -> MagicMock:
            captured["url"] = url
            return _mock_response(json_body={"data": {"investigation_id": "inv-1"}})

        monkeypatch.setattr("app.utils.ingest_delivery.httpx.post", _capture)
        ingest_delivery.send_ingest({"thread_id": "t-1"})
        assert captured["url"] == "https://fallback.example.com/api/investigations/ingest"

    def test_returns_none_when_response_has_no_investigation_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TRACER_INGEST_TOKEN", "tok")
        monkeypatch.setenv("TRACER_API_URL", "https://api.example.com")
        monkeypatch.setattr(
            "app.utils.ingest_delivery.httpx.post",
            lambda *_a, **_kw: _mock_response(json_body={"data": {}}),
        )
        assert ingest_delivery.send_ingest({"thread_id": "t-1"}) is None

    def test_http_status_error_returns_none_and_logs(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("TRACER_INGEST_TOKEN", "tok")
        monkeypatch.setenv("TRACER_API_URL", "https://api.example.com")
        monkeypatch.setattr(
            "app.utils.ingest_delivery.httpx.post",
            lambda *_a, **_kw: _mock_response(status_code=500, text="server err"),
        )
        with caplog.at_level("WARNING"):
            assert ingest_delivery.send_ingest({"thread_id": "t-1"}) is None
        assert any("Delivery HTTP failure" in r.message for r in caplog.records)

    def test_generic_exception_returns_none_and_logs(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("TRACER_INGEST_TOKEN", "tok")
        monkeypatch.setenv("TRACER_API_URL", "https://api.example.com")

        def _raise(*_a: Any, **_kw: Any) -> Any:
            raise ConnectionError("dns failure")

        monkeypatch.setattr("app.utils.ingest_delivery.httpx.post", _raise)
        with caplog.at_level("WARNING"):
            assert ingest_delivery.send_ingest({"thread_id": "t-1"}) is None
        assert any("Delivery failed" in r.message for r in caplog.records)
