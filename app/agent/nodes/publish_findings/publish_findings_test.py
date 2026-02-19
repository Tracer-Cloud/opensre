from app.agent.nodes.publish_findings.node import generate_report
from app.agent.state import InvestigationState


def test_publish_findings_includes_cited_evidence_section() -> None:
    state: InvestigationState = {
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
    # CloudWatch URL should be present (format may vary between Slack and terminal)
    assert "https://example.com/cloudwatch" in slack_message
    # Verify Data Lineage Flow section is present
    assert (
        "*Data Lineage Flow (Evidence-Based)*" in slack_message
        or "*Investigation Trace*" in slack_message
    )


def test_publish_findings_does_not_show_next_steps_sections() -> None:
    state: InvestigationState = {
        "pipeline_name": "demo-pipeline",
        "root_cause": "Schema change removed customer_id, causing downstream validation failure.",
        "confidence": 0.7,
        "validated_claims": [
            {"claim": "Schema version bumped to 2.0 without customer_id", "evidence_sources": []}
        ],
        "non_validated_claims": [],
        "validity_score": 0.8,
        "investigation_recommendations": ["Fetch CloudWatch metrics for spikes"],
        "remediation_steps": ["Add schema contract gate to block missing customer_id"],
        "context": {},
        "evidence": {},
        "raw_alert": {},
    }

    result = generate_report(state)
    slack_message = result["slack_message"]

    assert "*Suggested Next Steps:*" not in slack_message
    assert "*Remediation Next Steps:*" not in slack_message


def test_cited_evidence_dedup_by_evidence_id() -> None:
    state: InvestigationState = {
        "pipeline_name": "demo-pipeline",
        "root_cause": "Root cause text.",
        "confidence": 0.9,
        "validated_claims": [
            {
                "claim": "Claim one",
                "evidence_sources": ["s3_metadata"],
            },
            {
                "claim": "Claim two",
                "evidence_sources": ["s3_metadata"],
            },
        ],
        "non_validated_claims": [],
        "validity_score": 1.0,
        "evidence": {
            "s3_object": {
                "bucket": "demo-bucket",
                "key": "path/data.json",
                "found": True,
            },
            "s3": {},
        },
        "raw_alert": {},
    }

    result = generate_report(state)
    slack_message = result["slack_message"]

    # The short evidence id should appear once in cited evidence (prefixed with "- E")
    section = slack_message.split("*Cited Evidence:*", 1)[-1]
    evidence_lines = [line for line in section.splitlines() if line.startswith("- E")]
    assert len(evidence_lines) == 1
