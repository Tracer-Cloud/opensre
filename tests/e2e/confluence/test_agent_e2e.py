#!/usr/bin/env python3
"""End-to-end Confluence investigation pipeline smoke test.

Runs the investigation pipeline end to end with:
- a local HTTP Confluence stub
- a deterministic tool-planning LLM stub
- a deterministic diagnosis LLM stub

The goal is to verify that Confluence can be selected, queried, merged into
evidence, and surfaced in the final RCA report with citations.
"""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from app.pipeline.runners import run_investigation
from tests.utils.alert_factory import create_alert


class _FakeStructuredOutput:
    def __init__(self, plan_model):
        self._plan_model = plan_model

    def with_config(self, **_kwargs):
        return self

    def invoke(self, _prompt: str):
        return self._plan_model(
            actions=["search_confluence_docs"],
            rationale="Search Confluence for the runbook and operational notes first.",
        )


class _FakeToolLLM:
    def with_structured_output(self, plan_model):
        return _FakeStructuredOutput(plan_model)

    def with_config(self, **_kwargs):
        return self

    def invoke(self, _prompt: str):  # pragma: no cover - not used by the planner stub
        return SimpleNamespace(content="")


class _FakeReasoningLLM:
    def with_config(self, **_kwargs):
        return self

    def invoke(self, _prompt: str):
        return SimpleNamespace(
            content=(
                "ROOT_CAUSE: The runbook indicates the database connection failed because the "
                "service credentials were rotated without updating the pipeline.\n"
                "ROOT_CAUSE_CATEGORY: configuration_error\n"
                "VALIDATED_CLAIMS:\n"
                "- The incident response runbook documents the expected authentication flow\n"
                "- The recovery procedure points to expired credentials [evidence: confluence_docs]\n"
                "NON_VALIDATED_CLAIMS:\n"
                "- The database itself is unhealthy\n"
                "CAUSAL_CHAIN:\n"
                "- The Confluence runbook points to credential rotation as the required fix\n"
                "- The pipeline failed with an authentication error\n"
            )
        )


@pytest.fixture()
def confluence_stub():
    request_log: list[str] = []
    response_payload = {
        "results": [
            {
                "id": "12345",
                "title": "Pipeline Load Failure Runbook",
                "excerpt": "Database auth failures: confirm credentials and rotation history.",
                "url": "/wiki/spaces/SRE/pages/12345/Pipeline+Load+Failure+Runbook",
                "space_key": "SRE",
                "space_name": "Site Reliability Engineering",
                "labels": ["runbook", "incident", "ops"],
            }
        ],
        "totalSize": 1,
        "size": 1,
        "limit": 10,
        "_links": {"next": "", "prev": ""},
    }

    class FakeConfluenceHttpClient:
        def get(self, url: str, params: dict | None = None):  # noqa: ANN001
            request_log.append(f"{url}?{params or {}}")
            request = httpx.Request("GET", f"http://confluence.local{url}", params=params)
            return httpx.Response(200, json=response_payload, request=request)

        def close(self) -> None:
            return

    yield FakeConfluenceHttpClient(), request_log


def test_confluence_investigation_pipeline_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
    confluence_stub: tuple[str, list[str]],
) -> None:
    fake_client, request_log = confluence_stub

    plan_actions_module = importlib.import_module("app.nodes.plan_actions.plan_actions")
    diagnosis_module = importlib.import_module("app.nodes.root_cause_diagnosis.node")
    extract_module = importlib.import_module("app.nodes.extract_alert.extract")

    monkeypatch.setattr(plan_actions_module, "get_llm_for_tools", lambda: _FakeToolLLM())
    monkeypatch.setattr(diagnosis_module, "get_llm_for_reasoning", lambda: _FakeReasoningLLM())
    monkeypatch.setattr(extract_module, "get_llm_for_reasoning", lambda: _FakeReasoningLLM())
    monkeypatch.setattr("app.nodes.publish_findings.node.send_ingest", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.render_report",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.nodes.publish_findings.node.open_in_editor",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.utils.slack_delivery.send_slack_report",
        lambda *_args, **_kwargs: (False, "not sent"),
    )
    monkeypatch.setattr(
        "app.integrations.clients.confluence.client._get_client",
        lambda _config: fake_client,
    )

    raw_alert = create_alert(
        pipeline_name="events_fact",
        run_name="confluence-smoke",
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name="PipelineLoadFailure",
        annotations={
            "error_message": "Database connection failed with authentication error.",
            "context_sources": "confluence",
        },
    )

    resolved_integrations = {
        "confluence": {
            "base_url": "http://confluence.local",
            "email": "ops@example.com",
            "api_token": "token",
            "space_key": "SRE",
            "connection_verified": True,
        }
    }

    state = run_investigation(
        alert_name="PipelineLoadFailure",
        pipeline_name="events_fact",
        severity="critical",
        raw_alert=raw_alert,
        resolved_integrations=resolved_integrations,
    )

    evidence = state.get("evidence", {})
    report = state.get("report", "")
    validated_claims = state.get("validated_claims", [])

    assert any("/wiki/rest/api/content/search" in path for path in request_log), (
        f"Confluence search endpoint was not called: {request_log}"
    )
    assert evidence.get("confluence_docs"), "Expected Confluence search results in evidence"
    assert any(
        "confluence_docs" in (claim.get("evidence_sources") or [])
        for claim in validated_claims
    ), f"Validated claims should cite Confluence docs: {validated_claims}"
    assert "Pipeline Load Failure Runbook" in report, report
    assert "Cited Evidence" in report, report
    assert "confluence" in report.lower(), report
