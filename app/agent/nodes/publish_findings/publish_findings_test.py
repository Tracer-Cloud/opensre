from app.agent.nodes.publish_findings.node import generate_report


def test_publish_findings_includes_cited_evidence_section() -> None:
    state = {
        "pipeline_name": "demo-pipeline",
        "root_cause": "Root cause text.",
        "confidence": 0.82,
        "validated_claims": [
            {
                "claim": "Error logs show a failure during execution.",
                "evidence_sources": ["logs", "cloudwatch_logs"],
                "validation_status": "validated",
            }
        ],
        "non_validated_claims": [],
        "validity_score": 1.0,
        "context": {
            "tracer_web_run": {
                "status": "failed",
                "run_name": "run-123",
                "pipeline_name": "demo-pipeline",
                "run_cost": 1.23,
                "max_ram_gb": 2.0,
                "user_email": "user@example.com",
                "team": "demo-team",
                "instance_type": "m5.large",
            }
        },
        "evidence": {
            "error_logs": [{"message": "Failure in step 3"}],
            "total_logs": 1,
            "cloudwatch_logs": ["cloudwatch error line"],
        },
        "raw_alert": {"cloudwatch_logs_url": "https://example.com/cloudwatch"},
    }

    result = generate_report(state)
    slack_message = result["slack_message"]

    # Verify cited evidence section is present and contains actual evidence
    assert "*Cited Evidence:*" in slack_message
    # No longer shows generic Tracer Platform link - only actual evidence sources
    assert "CloudWatch Logs: https://example.com/cloudwatch" in slack_message
    assert '"message": "Failure in step 3"' in slack_message
    # Verify Data Lineage Flow section is present
    assert (
        "*Data Lineage Flow (Evidence-Based)*" in slack_message
        or "*Investigation Trace*" in slack_message
    )
