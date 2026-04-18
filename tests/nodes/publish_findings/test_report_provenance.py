from __future__ import annotations

from app.nodes.publish_findings.formatters.report import format_slack_message
from app.nodes.publish_findings.report_context import build_report_context


def _make_state() -> dict:
    return {
        "pipeline_name": "checkout-service",
        "alert_name": "Checkout latency spike",
        "root_cause": "Checkout service was throttled by the upstream API cluster.",
        "root_cause_category": "dependency_failure",
        "validity_score": 0.91,
        "validated_claims": [
            {
                "claim": "Grafana logs show repeated 500 responses.",
                "evidence_sources": ["grafana_logs"],
            }
        ],
        "non_validated_claims": [],
        "investigation_recommendations": [],
        "remediation_steps": [],
        "available_sources": {
            "grafana": {
                "grafana_endpoint": "https://myorg.grafana.net",
                "service_name": "checkout-api",
                "pipeline_name": "checkout-service",
            },
            "eks": {
                "cluster_name": "prod-cluster",
                "namespace": "payments",
                "region": "us-east-1",
            },
        },
        "evidence": {
            "grafana_logs": [
                {"message": "service unavailable"},
            ],
        },
    }


def test_build_report_context_adds_source_provenance() -> None:
    ctx = build_report_context(_make_state())

    assert ctx["source_provenance"]["grafana"]["summary"] == (
        "instance=myorg.grafana.net, service=checkout-api, pipeline=checkout-service"
    )
    assert ctx["evidence_catalog"]["evidence/grafana/loki"]["provenance"] == (
        "instance=myorg.grafana.net, service=checkout-api, pipeline=checkout-service"
    )


def test_format_slack_message_shows_provenance() -> None:
    ctx = build_report_context(_make_state())
    message = format_slack_message(ctx)

    assert "*Provenance:*" in message
    assert "Grafana: instance=myorg.grafana.net" in message
    assert "AWS EKS: cluster=prod-cluster, namespace=payments, region=us-east-1" in message
    assert (
        "provenance: instance=myorg.grafana.net, service=checkout-api, pipeline=checkout-service"
        in message
    )


def test_build_report_context_adds_additional_source_provenance() -> None:
    state = _make_state()
    state["available_sources"].update(
        {
            "datadog": {
                "site": "datadoghq.eu",
                "default_query": "service:checkout",
                "kubernetes_context": {"namespace": "payments"},
            },
            "github": {
                "owner": "myorg",
                "repo": "checkout",
                "ref": "main",
            },
            "vercel": {
                "project_name": "checkout-web",
                "deployment_id": "dpl_123",
            },
            "s3": {
                "bucket": "tracer-artifacts",
                "prefix": "runs/checkout",
            },
        }
    )
    state["evidence"]["datadog_logs"] = [{"message": "5xx spike"}]

    ctx = build_report_context(state)

    assert ctx["source_provenance"]["datadog"]["summary"] == (
        "site=datadoghq.eu, query=service:checkout, namespace=payments"
    )
    assert ctx["source_provenance"]["github"]["summary"] == "repo=myorg/checkout, ref=main"
    assert (
        ctx["source_provenance"]["vercel"]["summary"]
        == "project=checkout-web, deployment_id=dpl_123"
    )
    assert (
        ctx["source_provenance"]["s3"]["summary"] == "bucket=tracer-artifacts, prefix=runs/checkout"
    )
    assert (
        ctx["evidence_catalog"]["evidence/datadog/logs"]["provenance"]
        == "site=datadoghq.eu, query=service:checkout, namespace=payments"
    )


def test_build_report_context_drops_empty_provenance_summaries() -> None:
    state = _make_state()
    state["available_sources"]["github"] = {}
    state["available_sources"]["coralogix"] = {"application_name": ""}

    ctx = build_report_context(state)

    assert "github" not in ctx["source_provenance"]
    assert "coralogix" not in ctx["source_provenance"]


def test_format_slack_message_sanitizes_provenance_content() -> None:
    state = _make_state()
    state["available_sources"]["grafana"]["service_name"] = "**checkout-api**"

    ctx = build_report_context(state)
    message = format_slack_message(ctx)

    assert "service=*checkout-api*" in message
    assert "service=**checkout-api**" not in message
