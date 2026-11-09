"""Tests for AWS Batch evidence mapping."""

from integrations.tracer.tools.tracer_failed_jobs_tool import _map_failed_jobs


def test_failed_jobs_are_recorded_as_evidence() -> None:
    evidence = {}

    _map_failed_jobs(evidence, {"failed_jobs": [{"job_name": "job-1"}]}, {})

    assert evidence["catalog_entries"] == [
        {
            "source": "get_failed_jobs",
            "label": "Failed AWS Batch Jobs",
            "summary": "1 failed job",
            "url": None,
            "snippet": None,
        }
    ]
