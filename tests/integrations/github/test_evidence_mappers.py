from __future__ import annotations

from typing import Any

from integrations.github.tools.actions import (
    _map_get_github_actions_step_log,
    _map_list_github_actions_active_runs,
    _map_list_github_actions_run_jobs,
    _map_list_github_actions_workflow_runs,
)
from integrations.github.tools.commits import _map_list_github_commits
from integrations.github.tools.community_followup_tool import _map_summarize_community_followups
from integrations.github.tools.file_contents import _map_get_github_file_contents
from integrations.github.tools.git_deploy_timeline_tool import _map_get_git_deploy_timeline
from integrations.github.tools.issues import _map_search_github_issues
from integrations.github.tools.repository import _map_get_github_repository
from integrations.github.tools.repository_tree import _map_get_github_repository_tree
from integrations.github.tools.search_code import _map_search_github_code
from integrations.github.tools.stargazers import _map_get_github_star_history
from integrations.github.tools.work_status import (
    _map_list_github_security_alerts,
    _map_list_github_work_items,
    _map_summarize_github_pr_status,
)
from integrations.github.tools.work_status_report_tool import _map_generate_work_status_report


def test_map_get_github_actions_step_log() -> None:
    evidence_single: dict[str, Any] = {}
    _map_get_github_actions_step_log(
        evidence_single, {"log_text": "Error", "returned_lines": 1}, {}
    )
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_actions_step_log"
    assert entries[0]["summary"] == "1 line"

    evidence_plural: dict[str, Any] = {}
    _map_get_github_actions_step_log(
        evidence_plural, {"log_text": "Error\nTraceback", "returned_lines": 2}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 lines"


def test_map_get_github_file_contents() -> None:
    evidence: dict[str, Any] = {}
    output = {"file": {"content": "def main(): pass"}, "path": "src/main.py"}
    _map_get_github_file_contents(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_file_contents"
    assert entries[0]["summary"] == "File: src/main.py"


def test_map_get_github_repository() -> None:
    evidence: dict[str, Any] = {}
    output = {"repository": {"stargazers_count": 42}, "owner": "Tracer-Cloud", "repo": "opensre"}
    _map_get_github_repository(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_repository"
    assert entries[0]["summary"] == "Tracer-Cloud/opensre"


def test_map_get_github_repository_tree() -> None:
    evidence_single: dict[str, Any] = {}
    _map_get_github_repository_tree(
        evidence_single, {"tree": {"tree": [{"path": "README.md"}]}}, {}
    )
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_repository_tree"
    assert entries[0]["summary"] == "1 item"

    evidence_plural: dict[str, Any] = {}
    _map_get_github_repository_tree(
        evidence_plural, {"tree": {"tree": [{"path": "README.md"}, {"path": "src"}]}}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 items"


def test_map_generate_work_status_report() -> None:
    evidence: dict[str, Any] = {}
    output = {"slack_text": "Here is your report."}
    _map_generate_work_status_report(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "generate_work_status_report"
    assert entries[0]["summary"] == "Engineering work status report"


def test_map_get_git_deploy_timeline() -> None:
    evidence_single: dict[str, Any] = {}
    _map_get_git_deploy_timeline(evidence_single, {"commits": [{"commit": "abc"}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_git_deploy_timeline"
    evidence_plural: dict[str, Any] = {}
    _map_get_git_deploy_timeline(
        evidence_plural, {"commits": [{"commit": "abc"}, {"commit": "def"}]}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 deploys"


def test_map_get_github_star_history() -> None:
    evidence_single: dict[str, Any] = {}
    _map_get_github_star_history(
        evidence_single, {"daily": [{"date": "2026-08-01", "stars": 5}]}, {}
    )
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "get_github_star_history"
    assert entries[0]["summary"] == "1 day recorded"

    evidence_plural: dict[str, Any] = {}
    _map_get_github_star_history(evidence_plural, {"daily": [{"date": "1"}, {"date": "2"}]}, {})
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 days recorded"


def test_map_list_github_actions_workflow_runs() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_actions_workflow_runs(evidence_single, {"workflow_runs": [{"id": 42}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_actions_workflow_runs"
    assert entries[0]["summary"] == "1 run"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_actions_workflow_runs(
        evidence_plural, {"workflow_runs": [{"id": 42}, {"id": 43}]}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 runs"


def test_map_list_github_actions_active_runs() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_actions_active_runs(evidence_single, {"workflow_runs": [{"id": 1}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_actions_active_runs"
    assert entries[0]["summary"] == "1 run"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_actions_active_runs(
        evidence_plural, {"workflow_runs": [{"id": 1}, {"id": 2}]}, {}
    )
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 runs"


def test_map_list_github_actions_run_jobs() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_actions_run_jobs(evidence_single, {"jobs": [{"id": 101}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_actions_run_jobs"
    assert entries[0]["summary"] == "1 job"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_actions_run_jobs(evidence_plural, {"jobs": [{"id": 1}, {"id": 2}]}, {})
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 jobs"


def test_map_list_github_commits() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_commits(evidence_single, {"commits": [{"sha": "abc"}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_commits"
    assert entries[0]["summary"] == "1 commit"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_commits(evidence_plural, {"commits": [{"sha": "abc"}, {"sha": "def"}]}, {})
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 commits"


def test_map_list_github_security_alerts() -> None:
    evidence_single: dict[str, Any] = {}
    _map_list_github_security_alerts(evidence_single, {"alerts": [{"id": "alert1"}]}, {})
    entries = evidence_single.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_security_alerts"
    assert entries[0]["summary"] == "1 alert"

    evidence_plural: dict[str, Any] = {}
    _map_list_github_security_alerts(evidence_plural, {"alerts": [{"id": "a"}, {"id": "b"}]}, {})
    assert evidence_plural.get("catalog_entries", [])[0]["summary"] == "2 alerts"


def test_map_list_github_work_items() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "items": [
            {"number": 1, "title": "Issue 1", "work_status": "taken"},
            {"number": 2, "title": "Issue 2", "work_status": "up_for_grabs"},
            {"number": 3, "title": "Issue 3", "work_status": "unassigned"},
            {"number": 4, "title": "Issue 4", "work_status": "taken"},
            {"number": 5, "title": "Issue 5", "work_status": "unassigned"},
        ],
        "counts": {"taken": 2, "up_for_grabs": 1, "unassigned": 2},
    }
    _map_list_github_work_items(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "list_github_work_items"
    assert entries[0]["summary"] == "5 items: 2 taken, 1 up for grabs, 2 unassigned"


def test_map_summarize_github_pr_status() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "pull_requests": [
            {"number": 1, "title": "PR 1", "status": "mergeable"},
            {"number": 2, "title": "PR 2", "status": "blocked"},
            {"number": 3, "title": "PR 3", "status": "unknown"},
        ],
        "counts": {"mergeable": 1, "blocked": 1, "unknown": 1},
    }
    _map_summarize_github_pr_status(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "summarize_github_pr_status"
    assert entries[0]["summary"] == "3 PRs: 1 mergeable, 1 blocked, 1 unknown"


def test_map_search_github_issues() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "issues": [
            {"number": 1, "title": "Crash on startup"},
            {"number": 2, "title": "Memory leak"},
            {"number": 3, "title": "Timeout error"},
            {"number": 4, "title": "Exception in handler"},
        ],
        "query": "crash OR error OR exception",
    }
    _map_search_github_issues(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "search_github_issues"
    assert entries[0]["summary"] == "4 matches: crash OR error OR exception"


def test_map_search_github_code() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "matches": [
            {"path": "src/main.py", "repository": "test/repo"},
            {"path": "src/utils.py", "repository": "test/repo"},
            {"path": "config.yaml", "repository": "test/repo"},
            {"path": "README.md", "repository": "test/repo"},
        ],
        "query": "exception OR error",
    }
    _map_search_github_code(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "search_github_code"
    assert entries[0]["summary"] == "4 matches: exception OR error"


def test_map_summarize_community_followups() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "counts": {"unanswered_questions": 2, "agenda_items": 1, "comments": 10},
        "unanswered_questions": [{"issue_number": 1}, {"issue_number": 2}],
        "agenda_items": [{"issue_number": 3}],
    }
    _map_summarize_community_followups(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 1
    assert entries[0]["source"] == "summarize_community_followups"
    assert entries[0]["summary"] == "2 unanswered questions, 1 agenda item"


def test_map_summarize_community_followups_empty() -> None:
    evidence: dict[str, Any] = {}
    output = {
        "counts": {"unanswered_questions": 0, "agenda_items": 0, "comments": 10},
        "unanswered_questions": [],
        "agenda_items": [],
    }
    _map_summarize_community_followups(evidence, output, {})

    entries = evidence.get("catalog_entries", [])
    assert len(entries) == 0
