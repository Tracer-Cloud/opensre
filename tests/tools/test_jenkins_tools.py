"""Tests for the Jenkins evidence mappers."""

from __future__ import annotations

from typing import Any

from integrations.jenkins.tools._evidence import (
    map_get_jenkins_build_log,
    map_get_jenkins_pipeline_stages,
    map_list_jenkins_builds,
    map_list_jenkins_jobs,
    map_list_jenkins_running_builds,
)

# ---------------------------------------------------------------------------
# list_jenkins_builds
# ---------------------------------------------------------------------------


def test_map_list_jenkins_builds_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_builds(
        evidence,
        {
            "available": True,
            "job": "deploy-prod",
            "builds": [
                {"number": 42, "status": "SUCCESS"},
                {"number": 41, "status": "FAILURE"},
            ],
            "total": 2,
        },
        {"limit": 10},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "list_jenkins_builds"
    assert entries[0]["summary"] == "2 build(s), 1 failed for 'deploy-prod'"


def test_map_list_jenkins_builds_qualifies_when_page_is_saturated() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_builds(
        evidence,
        {
            "available": True,
            "job": "deploy-prod",
            "builds": [{"number": i, "status": "SUCCESS"} for i in range(10)],
            "total": 10,
        },
        {"limit": 10},
    )
    assert evidence["catalog_entries"][0]["summary"].startswith("10+ build(s), 0+ failed")


def test_map_list_jenkins_builds_cites_status_filter_instead_of_failed_count() -> None:
    """Regression: when a status filter is set, every returned build already
    matches it, so a derived failed count from an unfiltered read would be
    misleading (e.g. state='success' showing '0 failed'). The count itself
    is also always qualified: the client scans only the 50 most recent
    builds before filtering, regardless of `limit`, so history beyond that
    window could hold more matches that were never seen."""
    evidence: dict[str, Any] = {}
    map_list_jenkins_builds(
        evidence,
        {
            "available": True,
            "job": "deploy-prod",
            "builds": [{"number": 1, "status": "SUCCESS"}],
            "total": 1,
        },
        {"limit": 10, "status": "SUCCESS"},
    )
    assert (
        evidence["catalog_entries"][0]["summary"]
        == "1+ build(s) with status 'SUCCESS' for 'deploy-prod'"
    )


def test_map_list_jenkins_builds_qualifies_against_the_clients_hard_cap_not_a_larger_limit() -> (
    None
):
    """Regression: the client never fetches or returns more than 50 builds
    regardless of the caller's requested `limit` -- comparing only against
    `limit` (e.g. 1000) would miss that the real ceiling actually hit was
    50, silently reporting a saturated page as an exact count."""
    evidence: dict[str, Any] = {}
    map_list_jenkins_builds(
        evidence,
        {
            "available": True,
            "job": "deploy-prod",
            "builds": [{"number": i, "status": "SUCCESS"} for i in range(50)],
            "total": 50,
        },
        {"limit": 1000},
    )
    assert evidence["catalog_entries"][0]["summary"].startswith("50+ build(s)")


def test_map_list_jenkins_builds_skips_empty_and_unavailable() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_builds(evidence, {"available": True, "builds": [], "total": 0}, {})
    assert "catalog_entries" not in evidence

    evidence2: dict[str, Any] = {}
    map_list_jenkins_builds(evidence2, {"available": False, "error": "401"}, {})
    assert "catalog_entries" not in evidence2


# ---------------------------------------------------------------------------
# get_jenkins_build_log
# ---------------------------------------------------------------------------


def test_map_get_jenkins_build_log_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_jenkins_build_log(
        evidence,
        {
            "available": True,
            "job": "deploy-prod",
            "build_number": 42,
            "log": "line1\nline2",
            "truncated": False,
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_jenkins_build_log"
    assert entries[0]["summary"] == "11 char(s) of console log, for 'deploy-prod' #42"


def test_map_get_jenkins_build_log_notes_truncation() -> None:
    evidence: dict[str, Any] = {}
    map_get_jenkins_build_log(
        evidence,
        {"available": True, "job": "j", "build_number": 1, "log": "x" * 100, "truncated": True},
        {},
    )
    assert "truncated to tail" in evidence["catalog_entries"][0]["summary"]


def test_map_get_jenkins_build_log_skips_empty_log() -> None:
    evidence: dict[str, Any] = {}
    map_get_jenkins_build_log(evidence, {"available": True, "log": ""}, {})
    assert "catalog_entries" not in evidence


# ---------------------------------------------------------------------------
# get_jenkins_pipeline_stages
# ---------------------------------------------------------------------------


def test_map_get_jenkins_pipeline_stages_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_get_jenkins_pipeline_stages(
        evidence,
        {
            "available": True,
            "is_pipeline": True,
            "job": "deploy-prod",
            "build_number": 42,
            "status": "FAILED",
            "stages": [
                {"name": "Build", "status": "SUCCESS"},
                {"name": "Deploy", "status": "FAILED"},
            ],
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "get_jenkins_pipeline_stages"
    assert entries[0]["summary"] == (
        "2 stage(s), 1 failed (build status: FAILED) for 'deploy-prod' #42"
    )


def test_map_get_jenkins_pipeline_stages_skips_freestyle_job() -> None:
    """Regression: a freestyle job with no pipeline stages is an expected,
    unremarkable outcome, not a finding worth citing."""
    evidence: dict[str, Any] = {}
    map_get_jenkins_pipeline_stages(
        evidence, {"available": True, "is_pipeline": False, "stages": []}, {}
    )
    assert "catalog_entries" not in evidence


# ---------------------------------------------------------------------------
# list_jenkins_jobs
# ---------------------------------------------------------------------------


def test_map_list_jenkins_jobs_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_jobs(
        evidence,
        {
            "available": True,
            "jobs": [
                {"name": "job-a", "status": "SUCCESS"},
                {"name": "job-b", "status": "FAILURE"},
            ],
            "total": 2,
            "truncated": False,
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "list_jenkins_jobs"
    assert entries[0]["summary"] == "2 job(s), 1 failing"


def test_map_list_jenkins_jobs_qualifies_when_truncated() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_jobs(
        evidence,
        {
            "available": True,
            "jobs": [{"name": "job-a", "status": "SUCCESS"}],
            "total": 1,
            "truncated": True,
        },
        {},
    )
    assert evidence["catalog_entries"][0]["summary"] == "1+ job(s), 0+ failing"


def test_map_list_jenkins_jobs_skips_empty() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_jobs(
        evidence, {"available": True, "jobs": [], "total": 0, "truncated": False}, {}
    )
    assert "catalog_entries" not in evidence


def test_map_list_jenkins_jobs_cites_empty_result_when_truncated() -> None:
    """Every matching job beyond the folder-depth boundary still yields an
    empty list -- but ``truncated`` staying True means jobs do exist and
    were missed, which is worth citing rather than silently dropping."""
    evidence: dict[str, Any] = {}
    map_list_jenkins_jobs(
        evidence, {"available": True, "jobs": [], "total": 0, "truncated": True}, {}
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["summary"] == "0+ job(s), 0+ failing"


# ---------------------------------------------------------------------------
# list_jenkins_running_builds
# ---------------------------------------------------------------------------


def test_map_list_jenkins_running_builds_records_entry() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_running_builds(
        evidence,
        {
            "available": True,
            "running_builds": [{"job": "job-a", "number": 5}],
            "total": 1,
            "truncated": False,
        },
        {},
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["source"] == "list_jenkins_running_builds"
    assert entries[0]["summary"] == "1 build(s) currently running"


def test_map_list_jenkins_running_builds_qualifies_when_truncated() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_running_builds(
        evidence,
        {"available": True, "running_builds": [{"job": "a"}], "total": 1, "truncated": True},
        {},
    )
    assert evidence["catalog_entries"][0]["summary"] == "1+ build(s) currently running"


def test_map_list_jenkins_running_builds_skips_empty_and_unavailable() -> None:
    evidence: dict[str, Any] = {}
    map_list_jenkins_running_builds(
        evidence, {"available": True, "running_builds": [], "total": 0, "truncated": False}, {}
    )
    assert "catalog_entries" not in evidence

    evidence2: dict[str, Any] = {}
    map_list_jenkins_running_builds(evidence2, {"available": False, "error": "timeout"}, {})
    assert "catalog_entries" not in evidence2


def test_map_list_jenkins_running_builds_cites_empty_result_when_truncated() -> None:
    """Every running build beyond the folder-depth boundary still yields an
    empty list -- but ``truncated`` staying True means running builds do
    exist and were missed, which is worth citing rather than silently
    dropping."""
    evidence: dict[str, Any] = {}
    map_list_jenkins_running_builds(
        evidence, {"available": True, "running_builds": [], "total": 0, "truncated": True}, {}
    )
    entries = evidence["catalog_entries"]
    assert len(entries) == 1
    assert entries[0]["summary"] == "0+ build(s) currently running"
