"""Tests for the cross-repository GitHub CI health scan tool."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import pytest

from integrations.github.client import GitHubApiError
from integrations.github.tools.ci_health_scan import tool as tool_module
from integrations.github.tools.ci_health_scan.classify import classify_rollup
from integrations.github.tools.ci_health_scan.models import HeadKind, RepoRef
from integrations.github.tools.ci_health_scan.scan import (
    scan_batch,
    scan_repositories,
)
from integrations.github.tools.ci_health_scan.scope import resolve_scope
from integrations.github.tools.ci_health_scan.tool import TOOL_NAME, scan_github_ci_health
from tests.tools.conftest import BaseToolContract

Responder = Callable[[str, dict[str, Any]], dict[str, Any]]


class FakeGraphQLClient:
    """Stands in for ``GitHubRestClient``; routes each document by its operation name."""

    def __init__(self, responder: Responder) -> None:
        self._responder = responder
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def request(
        self, method: str, path: str, *, body: dict[str, Any] | None = None, **_: Any
    ) -> Any:
        assert method == "POST" and path == "/graphql"
        assert body is not None
        query = str(body["query"])
        operation = query.split("(", 1)[0].split("{", 1)[0].replace("query", "").strip()
        variables = dict(body.get("variables") or {})
        with self._lock:
            self.calls.append((operation, variables))
        return self._responder(operation, variables)


def _check_run(name: str, conclusion: str, url: str = "https://ci/x") -> dict[str, Any]:
    return {"__typename": "CheckRun", "name": name, "conclusion": conclusion, "detailsUrl": url}


def _status(context: str, state: str) -> dict[str, Any]:
    return {"__typename": "StatusContext", "context": context, "state": state, "targetUrl": ""}


def _rollup(
    *nodes: dict[str, Any],
    total: int | None = None,
    state: str = "FAILURE",
    next_cursor: str | None = None,
) -> dict[str, Any]:
    rollup: dict[str, Any] = {
        "state": state,
        "contexts": {
            "totalCount": total if total is not None else len(nodes),
            "nodes": list(nodes),
        },
    }
    if next_cursor is not None:
        rollup["contexts"]["pageInfo"] = {"hasNextPage": True, "endCursor": next_cursor}
    return rollup


def _commit(rollup: dict[str, Any] | None, sha: str = "abc1234") -> dict[str, Any]:
    return {"oid": sha * 5, "abbreviatedOid": sha, "statusCheckRollup": rollup}


def _pr(
    number: int, rollup: dict[str, Any] | None, *, fork: bool = False, draft: bool = False
) -> dict[str, Any]:
    return {
        "number": number,
        "title": f"PR {number}",
        "url": f"https://github.com/o/r/pull/{number}",
        "headRefName": f"feat/{number}",
        "isCrossRepository": fork,
        "isDraft": draft,
        "commits": {"nodes": [{"commit": _commit(rollup, sha=f"{number:07d}")}]},
    }


def _repository(
    *,
    default_rollup: dict[str, Any] | None = None,
    prs: list[dict[str, Any]] | None = None,
    total_prs: int | None = None,
    refs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pr_nodes = prs or []
    repo: dict[str, Any] = {
        "nameWithOwner": "o/r",
        "defaultBranchRef": {"name": "main", "target": _commit(default_rollup, sha="main000")},
        "pullRequests": {
            "totalCount": total_prs if total_prs is not None else len(pr_nodes),
            "nodes": pr_nodes,
        },
    }
    if refs is not None:
        repo["refs"] = refs
    return repo


def _ref(name: str, rollup: dict[str, Any] | None) -> dict[str, Any]:
    return {"name": name, "target": _commit(rollup, sha=name[:7].ljust(7, "0"))}


def _repos(*names: str) -> list[RepoRef]:
    return [RepoRef(owner="o", name=n) for n in names]


# --- classify -----------------------------------------------------------------


def test_classify_separates_failures_cancellations_and_dedupes_names() -> None:
    summary = classify_rollup(
        _rollup(
            _check_run("windows test", "FAILURE", url="https://ci/1"),
            _check_run("windows test", "FAILURE", url="https://ci/2"),
            _check_run("lint", "SUCCESS"),
            _check_run("e2e", "CANCELLED"),
            _status("Vercel", "ERROR"),
            _status("Aikido", "PENDING"),
            total=6,
        )
    )
    assert [c.name for c in summary.failing] == ["windows test", "Vercel"]
    assert summary.failing[0].url == "https://ci/1"
    assert summary.cancelled == ("e2e",)
    assert summary.truncated is False


def test_classify_flags_truncated_contexts_and_tolerates_missing_rollup() -> None:
    assert classify_rollup(None).is_failing is False
    truncated = classify_rollup(_rollup(_check_run("a", "TIMED_OUT"), total=150))
    assert truncated.truncated is True
    assert truncated.failing[0].state == "TIMED_OUT"


# --- scan ---------------------------------------------------------------------


def test_scan_batch_isolates_one_inaccessible_alias_and_keeps_the_rest() -> None:
    def respond(operation: str, _variables: dict[str, Any]) -> dict[str, Any]:
        assert operation == "RepoScan"
        return {
            "data": {
                "rateLimit": {"cost": 7, "remaining": 4000},
                "r0": _repository(
                    prs=[
                        _pr(1, _rollup(_check_run("unit", "FAILURE")), fork=True, draft=True),
                        _pr(2, _rollup(_check_run("unit", "SUCCESS"), state="SUCCESS")),
                    ],
                    total_prs=150,
                ),
                "r1": None,
                "r2": _repository(default_rollup=_rollup(_check_run("deploy", "STARTUP_FAILURE"))),
            },
            "errors": [{"path": ["r1"], "message": "Could not resolve to a Repository"}],
        }

    outcome = scan_batch(
        FakeGraphQLClient(respond),  # type: ignore[arg-type]
        _repos("alpha", "gone", "gamma"),
        include_all_branches=False,
    )
    alpha, gone, gamma = outcome.results
    assert outcome.rate_limit_cost == 7 and outcome.rate_limit_remaining == 4000

    assert alpha.scanned and alpha.open_prs == 150
    (head,) = alpha.failing_heads
    assert head.kind is HeadKind.PULL_REQUEST and head.number == 1
    assert head.is_fork and head.is_draft and head.sha == "0000001"
    assert [c.name for c in head.checks] == ["unit"]
    assert any("150 open PRs" in n for n in alpha.coverage_notices)

    assert not gone.scanned and gone.error == "Could not resolve to a Repository"

    (default_head,) = gamma.failing_heads
    assert default_head.kind is HeadKind.DEFAULT_BRANCH and default_head.ref == "main"


def test_scan_batch_follows_ref_pages_and_does_not_double_count_the_default_branch() -> None:
    def respond(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if operation == "RepoScan":
            assert variables["withRefs"] is True
            return {
                "data": {
                    "rateLimit": {"cost": 1, "remaining": 1},
                    "r0": _repository(
                        default_rollup=_rollup(_check_run("ci", "FAILURE")),
                        refs={
                            "totalCount": 3,
                            "pageInfo": {"hasNextPage": True, "endCursor": "c1"},
                            "nodes": [
                                _ref("main", _rollup(_check_run("ci", "FAILURE"))),
                                _ref("feat/a", _rollup(_check_run("ci", "FAILURE"))),
                            ],
                        },
                    ),
                }
            }
        assert operation == "RefsPage" and variables["after"] == "c1"
        return {
            "data": {
                "repository": {
                    "refs": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [_ref("feat/b", _rollup(_check_run("ci", "ACTION_REQUIRED")))],
                    }
                }
            }
        }

    (result,) = scan_batch(
        FakeGraphQLClient(respond),  # type: ignore[arg-type]
        _repos("alpha"),
        include_all_branches=True,
    ).results
    kinds = [(h.kind, h.ref) for h in result.failing_heads]
    assert kinds == [
        (HeadKind.DEFAULT_BRANCH, "main"),
        (HeadKind.BRANCH, "feat/a"),
        (HeadKind.BRANCH, "feat/b"),
    ]
    assert result.branches_seen == 3


def test_scan_batch_pages_through_checks_and_reports_heads_it_could_not_finish() -> None:
    """A failure on the second page of checks must not read as green.

    PR 1's first 100 checks pass and the failing one is on page two, so the
    scan follows the cursor and lists it. PR 2 has more checks than were
    returned but no cursor to follow: it stays out of the failing list, and the
    gap is a coverage notice instead of a silently dropped head. The follow-up
    page's cost joins the batch total.
    """
    green = [_check_run(f"unit-{i}", "SUCCESS") for i in range(100)]

    def respond(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if operation == "RepoScan":
            return {
                "data": {
                    "rateLimit": {"cost": 3, "remaining": 4000},
                    "r0": _repository(
                        prs=[
                            _pr(1, _rollup(*green, total=101, next_cursor="page2")),
                            _pr(2, _rollup(*green[:3], total=150)),
                        ]
                    ),
                }
            }
        assert operation == "CommitChecksPage"
        assert variables["oid"] == "0000001" * 5 and variables["after"] == "page2"
        return {
            "data": {
                "rateLimit": {"cost": 1, "remaining": 3999},
                "repository": {
                    "object": {
                        "statusCheckRollup": {
                            "contexts": {
                                "pageInfo": {"hasNextPage": False, "endCursor": None},
                                "nodes": [_check_run("slow-e2e", "FAILURE")],
                            }
                        }
                    }
                },
            }
        }

    outcome = scan_batch(
        FakeGraphQLClient(respond),  # type: ignore[arg-type]
        _repos("alpha"),
        include_all_branches=False,
    )
    (alpha,) = outcome.results
    (head,) = alpha.failing_heads
    assert head.number == 1 and [c.name for c in head.checks] == ["slow-e2e"]
    assert head.checks_truncated is False
    assert [n for n in alpha.coverage_notices if "0000002" in n and "150 checks" in n]
    assert outcome.rate_limit_cost == 4 and outcome.rate_limit_remaining == 3999


def test_scan_repositories_runs_batches_concurrently() -> None:
    """Three batches with concurrency three must all be in flight at once.

    A sequential fan-out never releases the barrier and the test fails on the
    barrier timeout instead of hanging.
    """
    barrier = threading.Barrier(3, timeout=5)

    def respond(_operation: str, _variables: dict[str, Any]) -> dict[str, Any]:
        barrier.wait()
        return {"data": {"rateLimit": {"cost": 2, "remaining": 9}, "r0": _repository()}}

    report = scan_repositories(
        FakeGraphQLClient(respond),  # type: ignore[arg-type]
        _repos("a", "b", "c"),
        owners=("o",),
        include_all_branches=False,
        concurrency=3,
        batch_size=1,
    )
    assert report.repos_scanned == 3
    assert report.rate_limit_cost == 6 and report.rate_limit_remaining == 9
    assert [r.repo for r in report.results] == ["o/a", "o/b", "o/c"]


def test_scan_repositories_marks_a_transport_failure_per_repo_without_aborting() -> None:
    def respond(_operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if variables.get("n0") == "broken":
            raise GitHubApiError("API rate limit already exceeded for user ID 1")
        return {"data": {"rateLimit": {"cost": 1, "remaining": 1}, "r0": _repository()}}

    report = scan_repositories(
        FakeGraphQLClient(respond),  # type: ignore[arg-type]
        _repos("broken", "fine"),
        owners=("o",),
        include_all_branches=False,
        batch_size=1,
    )
    assert report.repos_scanned == 1 and not report.all_failed
    assert report.errors == [
        {"repo": "o/broken", "error": "API rate limit already exceeded for user ID 1"}
    ]


# --- scope --------------------------------------------------------------------


def test_resolve_scope_defaults_to_viewer_and_orgs_and_stops_at_the_stale_cutoff() -> None:
    def respond(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if operation == "ViewerScope":
            return {
                "data": {"viewer": {"login": "me", "organizations": {"nodes": [{"login": "acme"}]}}}
            }
        assert operation == "OwnerRepos"
        login = variables["login"]
        if login == "me":
            nodes = [
                {
                    "name": "fresh",
                    "isPrivate": True,
                    "pushedAt": "2026-09-10T00:00:00Z",
                    "owner": {"login": "me"},
                },
                {
                    "name": "stale",
                    "isPrivate": False,
                    "pushedAt": "2020-01-01T00:00:00Z",
                    "owner": {"login": "me"},
                },
            ]
            # A second page exists but is older still; the cutoff must stop pagination.
            info = {"hasNextPage": True, "endCursor": "more"}
        else:
            nodes = [
                {
                    "name": "svc",
                    "isPrivate": True,
                    "pushedAt": "2026-09-01T00:00:00Z",
                    "owner": {"login": "acme"},
                }
            ]
            info = {"hasNextPage": False, "endCursor": None}
        return {"data": {"repositoryOwner": {"repositories": {"pageInfo": info, "nodes": nodes}}}}

    client = FakeGraphQLClient(respond)
    from datetime import UTC, datetime

    scope = resolve_scope(
        client,  # type: ignore[arg-type]
        owners=None,
        since_days=365,
        now=datetime(2026, 9, 14, tzinfo=UTC),
    )
    assert scope.owners == ("me", "acme")
    assert [r.full_name for r in scope.repos] == ["me/fresh", "acme/svc"]
    assert scope.skipped_stale == 1
    assert not any(variables.get("after") for op, variables in client.calls if op == "OwnerRepos")
    assert any("since_days=0" in n for n in scope.coverage_notices)


def _owner_page(*names: str) -> dict[str, Any]:
    nodes = [
        {"name": n, "isPrivate": True, "pushedAt": "2026-09-10T00:00:00Z", "owner": {"login": "x"}}
        for n in names
    ]
    page = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}
    return {"data": {"repositoryOwner": {"repositories": page}}}


def test_resolve_scope_follows_organization_pages() -> None:
    """A token in more than 100 organizations must not lose the later ones."""

    def respond(operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if operation == "ViewerScope":
            if variables.get("after") is None:
                orgs = {
                    "pageInfo": {"hasNextPage": True, "endCursor": "orgs2"},
                    "nodes": [{"login": "first"}],
                }
            else:
                assert variables["after"] == "orgs2"
                orgs = {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [{"login": "second"}],
                }
            return {"data": {"viewer": {"login": "me", "organizations": orgs}}}
        return _owner_page(variables["login"])

    scope = resolve_scope(FakeGraphQLClient(respond), owners=None, since_days=0)  # type: ignore[arg-type]
    assert scope.owners == ("me", "first", "second")
    assert [r.name for r in scope.repos] == ["me", "first", "second"]


def test_resolve_scope_keeps_valid_owners_when_one_cannot_be_resolved() -> None:
    """``owners=["acme", "typo"]`` scans acme and reports typo instead of failing the call."""

    def respond(_operation: str, variables: dict[str, Any]) -> dict[str, Any]:
        if variables["login"] == "typo":
            return {
                "data": {"repositoryOwner": None},
                "errors": [{"message": "Could not resolve to a RepositoryOwner"}],
            }
        return _owner_page("svc")

    scope = resolve_scope(FakeGraphQLClient(respond), owners=["acme", "typo"], since_days=0)  # type: ignore[arg-type]
    assert [r.full_name for r in scope.repos] == ["x/svc"]
    assert any("typo" in n and "RepositoryOwner" in n for n in scope.coverage_notices)


def test_resolve_scope_raises_when_every_owner_fails_the_same_way() -> None:
    """All owners unreadable (exhausted budget) is still one clear tool error, not an empty scan."""

    def respond(_operation: str, _variables: dict[str, Any]) -> dict[str, Any]:
        return {
            "data": None,
            "errors": [{"type": "RATE_LIMIT", "message": "API rate limit already exceeded"}],
        }

    with pytest.raises(GitHubApiError, match="rate limit"):
        resolve_scope(FakeGraphQLClient(respond), owners=["acme", "beta"], since_days=0)  # type: ignore[arg-type]


# --- tool ---------------------------------------------------------------------


def _install_client(monkeypatch: pytest.MonkeyPatch, responder: Responder) -> FakeGraphQLClient:
    client = FakeGraphQLClient(responder)

    def build_client(_token: str | None = None, **_: Any) -> FakeGraphQLClient:
        return client

    monkeypatch.setattr(tool_module, "GitHubRestClient", build_client)
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "token")
    return client


def test_tool_reports_missing_token_without_calling_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_module, "resolve_github_token", lambda _t=None: "")
    result = scan_github_ci_health()
    assert result["available"] is False
    assert "token" in result["error"]


def test_tool_turns_an_exhausted_graphql_budget_into_one_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def respond(_operation: str, _variables: dict[str, Any]) -> dict[str, Any]:
        return {
            "data": None,
            "errors": [
                {"type": "RATE_LIMIT", "message": "API rate limit already exceeded for user ID 1"}
            ],
        }

    _install_client(monkeypatch, respond)
    result = scan_github_ci_health(owners=["acme"])
    assert result["available"] is False
    assert "rate limit" in result["error"].lower()


def test_tool_returns_counts_notices_and_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    def respond(operation: str, _variables: dict[str, Any]) -> dict[str, Any]:
        if operation == "OwnerRepos":
            return {
                "data": {
                    "rateLimit": {"cost": 1, "remaining": 4994},
                    "repositoryOwner": {
                        "repositories": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [
                                {
                                    "name": "a",
                                    "isPrivate": True,
                                    "pushedAt": "2026-09-10T00:00:00Z",
                                    "owner": {"login": "acme"},
                                },
                                {
                                    "name": "b",
                                    "isPrivate": True,
                                    "pushedAt": "2026-09-10T00:00:00Z",
                                    "owner": {"login": "acme"},
                                },
                            ],
                        }
                    },
                }
            }
        assert operation == "RepoScan"
        return {
            "data": {
                "rateLimit": {"cost": 4, "remaining": 4990},
                "r0": _repository(prs=[_pr(7, _rollup(_check_run("unit", "FAILURE")))]),
                "r1": _repository(default_rollup=_rollup(_status("Vercel", "FAILURE"))),
            }
        }

    _install_client(monkeypatch, respond)
    result = scan_github_ci_health(owners=["acme"], since_days=0)
    assert result["success"] is True
    assert result["repos_scanned"] == 2 and result["owners"] == ["acme"]
    assert result["counts"] == {
        "failing_prs": 1,
        "failing_default_branches": 1,
        "failing_branches": 0,
    }
    assert result["failing_prs"][0]["number"] == 7
    assert result["failing_default_branches"][0]["repo"] == "acme/b"
    # Owner listing (1) plus the batch (4): every document's spend is counted.
    assert result["rate_limit_cost"] == 5 and result["rate_limit_remaining"] == 4990
    assert result["elapsed_seconds"] >= 0
    assert "1 of 1 open PRs failing CI" in result["summary"]


class TestScanGithubCiHealthContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return scan_github_ci_health.__opensre_registered_tool__

    def test_registered_name(self) -> None:
        assert self._tool().name == TOOL_NAME
