"""A finished demo's root cause analysis states only what its evidence shows."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from infrastructure.text.data_blob import is_data_blob
from integrations.github.tools.ci_repair_loop.models import RepairRun, RepairStatus
from integrations.github.tools.ci_repair_loop.storage import RepairStore
from integrations.github.tools.ci_repair_run.root_cause import (
    RepairEvidence,
    github_links,
    read_repair_evidence,
    render_analysis,
    root_cause_analysis,
)

_RUN_ID = "f50fd5579ff9"
_FAILING = "34b4b578a3ea7a822906534f3e664f3cfd6da984"
_FIX = "deaf6b28f36d80641f3304df411052421b603ce5"
_DIFF = (
    "diff --git a/calculator.py b/calculator.py\n"
    "index 12ee743..4693ad3 100644\n"
    "--- a/calculator.py\n"
    "+++ b/calculator.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def add(a, b):\n"
    "-    return a - b\n"
    "+    return a + b\n"
)


def _store(tmp_path: Path, *attempts: dict[str, Any]) -> RepairStore:
    """A finished seeded-demo run whose attempt records are ``attempts``, in order."""
    store = RepairStore(tmp_path)
    now = time.time()
    store.save(
        RepairRun(
            id=_RUN_ID,
            owner="alice",
            repo="opensre-ci-repair-demo-ab12",
            actor="alice",
            actor_id=1,
            started_at=now,
            deadline=now + 600,
            pr_number=1,
            fast_checks=True,
            status=RepairStatus.SUCCEEDED,
            attempts=len(attempts),
            reason="The repair commit passed CI.",
        )
    )
    store.directory(_RUN_ID).mkdir(parents=True, exist_ok=True)
    for number, record in enumerate(attempts, start=1):
        store.attempt_path(_RUN_ID, number).write_text(json.dumps(record), encoding="utf-8")
    return store


def test_the_fix_is_read_from_the_attempt_that_pushed_the_verified_commit(
    tmp_path: Path,
) -> None:
    # Arrange: attempt 1 pushed the fix; attempt 2 then found nothing left to fix
    store = _store(
        tmp_path,
        {
            "failing_checks": ["test"],
            "fix_head_sha": _FIX,
            "changed_files": ["calculator.py"],
            "summary": (
                "Updated `calculator.py` so `add()` performs addition. Ran the tests.\n\n"
                "No commit or push performed."
            ),
            "diff": _DIFF,
            "diff_truncated": False,
        },
        {"failing_checks": [], "error_kind": "no_failing_checks", "summary": "", "diff": ""},
    )

    # Act
    evidence = read_repair_evidence(_RUN_ID, _FIX, store=store)

    # Assert
    assert evidence.failing_checks == ("test",)
    assert evidence.on_seeded_fixture is True
    assert evidence.fix_files == ("calculator.py",)
    assert evidence.fix_summary == "Updated `calculator.py` so `add()` performs addition."
    assert evidence.fix_diff.splitlines()[-2:] == ["-    return a - b", "+    return a + b"]
    assert "diff --git" not in evidence.fix_diff


def test_an_unknown_run_reads_as_no_evidence(tmp_path: Path) -> None:
    assert read_repair_evidence(_RUN_ID, _FIX, store=RepairStore(tmp_path)) == RepairEvidence()


@pytest.mark.parametrize(
    ("seeded_here", "on_seeded_fixture", "named"),
    [(True, True, True), (False, True, False), (True, False, False)],
    ids=["committed-and-kept", "reused-pull-request", "head-replaced-mid-repair"],
)
def test_the_seeded_fault_is_named_only_for_a_commit_this_run_made_and_kept(
    seeded_here: bool, on_seeded_fixture: bool, named: bool
) -> None:
    analysis = root_cause_analysis(
        pr_number=1,
        outcome="success",
        failing_commit=_FAILING,
        fix_commit=_FIX,
        seeded_here=seeded_here,
        evidence=RepairEvidence(failing_checks=("test",), on_seeded_fixture=on_seeded_fixture),
    )

    assert ("cause" in analysis) is named
    if named:
        assert analysis["cause"].startswith(f"Commit {_FAILING[:7]} changed `add()`")


def test_a_failed_repair_claims_no_fix_in_its_links_or_its_analysis() -> None:
    # Arrange: the pull request head moved to an unverified commit, and a check passed
    unverified_head = "c0ffee0c0ffee0c0ffee0c0ffee0c0ffee0c0ff"

    # Act
    links = github_links(
        owner="alice",
        repo="demo",
        pr_number=1,
        pr_url="",
        failing_commit=_FAILING,
        failed_run_id=11,
        fix_commit=unverified_head,
        passing_run_id=22,
        verified=False,
    )
    analysis = root_cause_analysis(
        pr_number=1,
        outcome="failed",
        failing_commit=_FAILING,
        fix_commit=unverified_head,
        seeded_here=True,
        evidence=RepairEvidence(
            on_seeded_fixture=True, reason="Stopped after 3 failed repair attempts."
        ),
    )

    # Assert
    assert set(links) == {"pull_request", "failing_commit", "failed_run"}
    assert links["pull_request"] == "https://github.com/alice/demo/pull/1"
    assert analysis["fix"] == "No verified fix: Stopped after 3 failed repair attempts."
    assert "verification" not in analysis


def test_agent_text_cannot_hide_the_analysis_as_a_data_blob(tmp_path: Path) -> None:
    # Arrange: the coding agent's summary and diff both carry JSON key separators
    store = _store(
        tmp_path,
        {
            "failing_checks": ["test"],
            "fix_head_sha": _FIX,
            "changed_files": ["calculator.py"],
            "summary": 'Set {"op": "add", "mode": "sum"} in calculator.py.',
            "diff": '+CONFIG = {"op": "add", "mode": "sum"}\n',
            "diff_truncated": False,
        },
    )
    evidence = read_repair_evidence(_RUN_ID, _FIX, store=store)
    analysis = root_cause_analysis(
        pr_number=1,
        outcome="success",
        failing_commit=_FAILING,
        fix_commit=_FIX,
        seeded_here=True,
        evidence=evidence,
    )

    # Act
    text = render_analysis({"pull_request": "https://github.com/alice/demo/pull/1"}, analysis)

    # Assert: the gateway shows it, keeping the summary and dropping the diff
    assert not is_data_blob(text)
    assert "Set {'op': 'add'" in text
    assert "```diff" not in text
