"""Epoch boundaries, attribution, and historical GitHub snapshots."""

import json

import pytest

import integrations.github.ci_epochs as ci_epochs
from integrations.github.ci_epochs import Commit, build_epochs, commit_author, workflow_results


def test_red_streak_closes_at_first_green_and_next_red_is_disjoint():
    commits = [
        Commit(str(i), "opensre", result)
        for i, result in enumerate(["red", "red", "green", "green", "red", "green"])
    ]
    epochs = build_epochs("owner/repo", 1, commits)
    assert [[c.sha for c in e.commits] for e in epochs] == [["0", "1", "2"], ["4", "5"]]
    assert [e.fixing_commit.sha for e in epochs] == ["2", "5"]
    assert [e.outcome for e in epochs] == ["agent_fixed", "agent_fixed"]
    assert build_epochs("owner/repo", 1, commits) == epochs
    assert len({e.id for e in epochs}) == 2


def test_green_only_and_unresolved_histories():
    assert build_epochs("owner/repo", 1, [Commit("a", "user", "green")]) == []
    epoch = build_epochs("owner/repo", 1, [Commit("a", "user", "red"), Commit("b", "user", "red")])[
        0
    ]
    assert epoch.fixing_commit is None
    assert epoch.outcome == "unresolved"


def test_missing_ci_does_not_prove_an_agent_fix():
    epoch = build_epochs(
        "owner/repo",
        1,
        [
            Commit("a", "user", "red"),
            Commit("b", "user", "unknown"),
            Commit("c", "opensre", "green"),
        ],
    )[0]
    assert epoch.outcome == "incomplete"


def test_only_the_first_green_author_gets_credit():
    epoch = build_epochs(
        "owner/repo",
        1,
        [
            Commit("a", "opensre", "red"),
            Commit("b", "user", "green"),
            Commit("c", "opensre", "green"),
        ],
    )[0]
    assert epoch.outcome == "other_fixed"
    assert epoch.fixing_commit.sha == "b"


def test_coauthor_identity_and_untrusted_display_name():
    commit = {
        "author": {"login": "davincios"},
        "commit": {
            "author": {"name": "davincios", "email": "user@example.com"},
            "committer": {"name": "davincios", "email": "user@example.com"},
            "message": "Repair calculator\n\nCo-authored-by: OpenSRE Agent <opensreagent@opensre.com>",
        },
    }
    assert commit_author(commit) == "opensre"
    commit["commit"]["message"] = (
        "Mention Co-authored-by: OpenSRE Agent <opensreagent@opensre.com> in documentation"
    )
    commit["commit"]["author"]["name"] = "OpenSRE Agent"
    assert commit_author(commit) == "davincios"


def test_latest_workflow_attempt_pending_and_skipped_are_not_green():
    def run(identifier, sha, conclusion, *, status="completed", workflow=1, attempt=1):
        return {
            "id": identifier,
            "head_sha": sha,
            "workflow_id": workflow,
            "event": "pull_request",
            "run_attempt": attempt,
            "status": status,
            "conclusion": conclusion,
            "updated_at": "2026-09-15T21:32:04Z",
        }

    rows = [
        run(1, "a", "failure"),
        run(2, "a", "success"),
        run(3, "b", "failure"),
        run(3, "b", None, status="in_progress", attempt=2),
        run(4, "c", "skipped"),
    ]
    result = workflow_results(rows)
    assert result["a"][0] == "green"
    assert result["b"][0] == "pending"
    assert result["c"][0] == "unknown"
    assert "missing" not in result


class GitHubHistory:
    def __init__(self, size=2):
        self.history = [
            {"sha": str(index), "commit": {"author": {"email": "opensreagent@opensre.com"}}}
            for index in range(size)
        ]
        self.runs = [
            {
                "id": index + 1,
                "workflow_id": 1,
                "event": "pull_request",
                "pull_requests": [{"number": 1}],
                "head_sha": str(index),
                "status": "completed",
                "conclusion": "failure" if index < size - 1 else "success",
            }
            for index in range(size)
        ]
        self.calls = []
        self.total_runs = len(self.runs)

    def request(self, method, path, *, params):
        assert method == "GET"
        self.calls.append((path, params))
        if path.endswith("/pulls/1"):
            return {
                "base": {"sha": "base"},
                "head": {"sha": self.history[-1]["sha"], "ref": "branch"},
                "commits": len(self.history),
                "state": "closed",
                "merged": False,
            }
        start = (params["page"] - 1) * params["per_page"]
        end = start + params["per_page"]
        if "/compare/" in path:
            return {"total_commits": len(self.history), "commits": self.history[start:end]}
        if path.endswith("/commits"):
            return self.history[start:end]
        assert path.endswith("/actions/runs")
        return {"total_count": self.total_runs, "workflow_runs": self.runs[start:end]}


@pytest.fixture
def observe(monkeypatch, tmp_path):
    def create(history):
        def token():
            return "unused"

        def client(_token):
            return history

        monkeypatch.setattr(ci_epochs, "_github_token", token)
        monkeypatch.setattr(ci_epochs, "GitHubRestClient", client)
        return ci_epochs.Observer("owner", "repo", 1, tmp_path)

    return create


def test_start_after_repair_and_refresh_ci_without_refetching_commits(observe):
    history = GitHubHistory()
    history.runs[-1].update(status="in_progress", conclusion=None)
    observer = observe(history)
    observer.tick()
    assert observer.epochs[0].outcome == "unresolved"
    identity = observer.epochs[0].id
    history.runs[-1].update(status="completed", conclusion="success")
    observer.tick()
    assert observer.epochs[0].outcome == "agent_fixed"
    assert observer.epochs[0].id == identity
    assert len(history.calls) == 5  # Three initial requests, two on the next poll.
    restarted = observe(history)
    assert restarted.tick() is False  # Closed PR history is still reconstructable.
    assert restarted.epochs == observer.epochs
    saved = json.loads(restarted.path.read_text())
    assert saved["epochs"] == [epoch.record() for epoch in restarted.epochs]


def test_large_pr_uses_paginated_comparison(observe):
    history = GitHubHistory(251)
    observer = observe(history)
    observer.tick()
    assert len(observer.epochs) == 1
    assert len(observer.epochs[0].commits) == 251
    assert observer.epochs[0].fixing_commit.sha == "250"
    assert sum("/compare/" in path for path, _ in history.calls) == 3


def test_truncated_history_does_not_overwrite_valid_evidence(observe):
    history = GitHubHistory()
    observer = observe(history)
    observer.tick()
    previous = observer.path.with_suffix(".json").read_text()
    history.total_runs += 1
    with pytest.raises(ValueError, match="truncated"):
        observer.tick()
    assert observer.path.with_suffix(".json").read_text() == previous


def test_observer_excludes_push_runs_and_other_pull_requests(observe):
    history = GitHubHistory()
    passing = history.runs[-1]
    history.runs.extend(
        [
            dict(passing, id=100, event="push", conclusion="failure"),
            dict(passing, id=101, pull_requests=[{"number": 2}], conclusion="failure"),
            dict(passing, id=102, pull_requests=[], conclusion="failure"),
        ]
    )
    history.total_runs = len(history.runs)
    observer = observe(history)
    observer.tick()
    assert observer.epochs[0].outcome == "agent_fixed"
    assert observer.epochs[0].fixing_commit.sha == "1"
    assert all(
        params["event"] == "pull_request"
        for path, params in history.calls
        if path.endswith("/actions/runs")
    )

    history.runs.remove(passing)
    history.total_runs = len(history.runs)
    observer.tick()
    assert observer.commits[-1].result == "unknown"
    assert observer.epochs[0].outcome == "unresolved"
    assert history.calls[-1][1]["head_sha"] == "1"
    assert history.calls[-1][1]["event"] == "pull_request"


def test_publishing_keeps_stable_identity_and_limits_credit_to_this_repair(observe, monkeypatch):
    history = GitHubHistory(5)
    for index, run in enumerate(history.runs):
        run.update(
            conclusion="failure" if index in {0, 2} else "success",
            updated_at="2026-09-15T21:32:04Z",
        )
    observer = observe(history)
    observer.tick()
    captured = []

    class AnalyticsSink:
        def capture(self, event, properties):
            captured.append((event, properties))

    sink = AnalyticsSink()
    monkeypatch.setattr(ci_epochs, "get_analytics", lambda: sink)
    assert observer.publish(fixing_sha="3") == 1
    event, properties = captured[0]
    assert event == "opensre_ci_epoch_resolved"
    assert properties == {
        "epoch_id": observer.epochs[1].id,
        "repository": "owner/repo",
        "pr_number": 1,
        "first_red_sha": "2",
        "fixing_sha": "3",
        "fixer": "opensre",
        "outcome": "agent_fixed",
        "ci_source": "github_actions",
        "ci_updated_at": "2026-09-15T21:32:04Z",
        "attribution_source": "git_authorship",
    }
    assert observer.publish(fixing_sha="3") == 1
    assert captured[1] == captured[0]  # Consumer deduplicates replayed repair identities.
    assert observer.publish(fixing_sha="4") == 0  # Later green commits are not new fixes.
    observer.epochs = build_epochs("owner/repo", 1, [Commit("x", "opensre", "red")])
    assert observer.publish() == 0


@pytest.mark.parametrize("state", ["passed", "failed", "superseded"])
def test_repair_verification_publishes_only_a_confirmed_passing_fix(monkeypatch, state):
    from integrations.github.tools.ci_fix import runner
    from integrations.github.tools.ci_fix.context import CiFixContext
    from integrations.github.tools.ci_fix.ship import PushResult
    from integrations.github.tools.ci_fix.verification import CheckState, CheckVerification

    ctx = CiFixContext(
        owner="owner",
        repo="repo",
        number=1,
        title="",
        url="",
        base_branch="main",
        head_branch="repair",
        head_sha="red",
        skipped_check_names=(),
        failing_checks=(),
        task="",
    )
    published = []

    def verify(*_args, **_kwargs):
        return CheckVerification(CheckState(state), ("test",))

    def publish(*args, **kwargs):
        published.append((args, kwargs))

    monkeypatch.setattr(runner, "wait_for_pr_checks", verify)
    monkeypatch.setattr(runner, "record_verification", lambda *_args: None)
    monkeypatch.setattr(runner, "publish_repair_epoch", publish)
    runner._verify_repair(
        ctx,
        {"owner": "owner", "repo": "repo", "pr_number": 1},
        PushResult("repair", "green", ["fix.py"]),
        "test-token",
    )
    assert published == (
        [(("owner", "repo", 1), {"github_token": "test-token", "fixing_sha": "green"})]
        if state == "passed"
        else []
    )
