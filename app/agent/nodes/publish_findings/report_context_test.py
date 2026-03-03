from __future__ import annotations

import time

from app.agent.nodes.publish_findings.report_context import build_report_context
from app.agent.state import InvestigationState


def test_build_report_context_defaults_and_claim_filtering() -> None:
    state: InvestigationState = {
        "raw_alert": "not-a-dict",
        "validated_claims": [
            {"claim": "valid claim", "evidence_sources": []},
            {"claim": "   ", "evidence_sources": []},
            {"claim": "NON_ARTIFACT", "evidence_sources": []},
        ],
        "non_validated_claims": [],
    }

    context = build_report_context(state)

    assert context["pipeline_name"] == "unknown"
    assert context["raw_alert"] == {}
    assert len(context["validated_claims"]) == 1
    assert context["validated_claims"][0]["claim"] == "valid claim"
    assert context["validity_score"] == 0.0
    assert context["kube_failed_pods"] == []


def test_build_report_context_attaches_evidence_ids_and_labels() -> None:
    state: InvestigationState = {
        "pipeline_name": "etl-pipeline",
        "validated_claims": [
            {
                "claim": "Issue validated from multiple sources",
                "evidence_sources": ["s3_metadata", "cloudwatch", "datadog"],
            }
        ],
        "non_validated_claims": [],
        "evidence": {
            "s3_object": {
                "bucket": "demo-bucket",
                "key": "landing/data.json",
                "metadata": {
                    "schema_change_injected": True,
                    "schema_version": "2.0",
                },
            },
            "s3_audit_payload": {
                "bucket": "audit-bucket",
                "key": "audit/path.json",
                "content": {"event": "schema-change"},
            },
            "datadog_logs": [{"message": "PIPELINE_ERROR"}],
            "datadog_logs_query": "service:etl PIPELINE_ERROR",
        },
        "raw_alert": {"cloudwatch_logs_url": "https://example.com/cloudwatch"},
    }

    context = build_report_context(state)

    claim = context["validated_claims"][0]
    assert claim["evidence_ids"] == [
        "evidence/s3_metadata/landing",
        "evidence/cloudwatch/prefect",
        "evidence/datadog/logs",
    ]
    assert claim["evidence_labels"] == ["E1", "E3", "E4"]
    assert claim["evidence_sources"] == []
    assert "evidence/s3_audit/main" in context["evidence_catalog"]


def test_build_report_context_populates_duration_and_kube_fallbacks() -> None:
    state: InvestigationState = {
        "investigation_started_at": time.monotonic() - 2.0,
        "raw_alert": {
            "annotations": {
                "hostname": "pod-a",
                "container_name": "etl-container",
                "namespace": "tracer-test",
            }
        },
        "evidence": {},
        "validated_claims": [],
        "non_validated_claims": [],
    }

    context = build_report_context(state)

    assert isinstance(context["investigation_duration_seconds"], int)
    assert context["investigation_duration_seconds"] >= 0
    assert context["kube_pod_name"] == "pod-a"
    assert context["kube_container_name"] == "etl-container"
    assert context["kube_namespace"] == "tracer-test"
