"""GraphQL queries for the CI health scan, run through ``GitHubRestClient``."""

from __future__ import annotations

import threading
from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_health_scan.classify import CONTEXTS_PAGE_SIZE

GRAPHQL_PATH = "/graphql"
REPOS_PAGE_SIZE = 100
REFS_PAGE_SIZE = 100
ORGS_PAGE_SIZE = 100
MAX_OPEN_PRS = 100

#: Every document selects this so the tally below sees each round-trip's spend.
_RATE_LIMIT_FIELD = "rateLimit { cost remaining }"

_CONTEXT_NODES = """
      nodes {
        __typename
        ... on CheckRun { name conclusion detailsUrl }
        ... on StatusContext { context state targetUrl }
      }
"""

_HEAD_FRAGMENT = f"""
fragment HeadFields on Commit {{
  oid
  abbreviatedOid
  statusCheckRollup {{
    state
    contexts(first: {CONTEXTS_PAGE_SIZE}) {{
      totalCount
      pageInfo {{ hasNextPage endCursor }}
{_CONTEXT_NODES}
    }}
  }}
}}
"""

VIEWER_SCOPE_QUERY = f"""
query ViewerScope($after: String) {{
  {_RATE_LIMIT_FIELD}
  viewer {{
    login
    organizations(first: {ORGS_PAGE_SIZE}, after: $after) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ login }}
    }}
  }}
}}
"""

OWNER_REPOS_QUERY = f"""
query OwnerRepos($login: String!, $after: String, $privacy: RepositoryPrivacy) {{
  {_RATE_LIMIT_FIELD}
  repositoryOwner(login: $login) {{
    repositories(
      first: {REPOS_PAGE_SIZE}
      after: $after
      isArchived: false
      privacy: $privacy
      ownerAffiliations: [OWNER]
      orderBy: {{field: PUSHED_AT, direction: DESC}}
    ) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        name
        isPrivate
        pushedAt
        owner {{ login }}
      }}
    }}
  }}
}}
"""

_REPO_FRAGMENT = f"""
fragment RepoFields on Repository {{
  nameWithOwner
  defaultBranchRef {{ name target {{ ...HeadFields }} }}
  pullRequests(states: OPEN, first: {MAX_OPEN_PRS}, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
    totalCount
    nodes {{
      number
      title
      url
      headRefName
      isCrossRepository
      isDraft
      commits(last: 1) {{ nodes {{ commit {{ ...HeadFields }} }} }}
    }}
  }}
  refs(refPrefix: "refs/heads/", first: {REFS_PAGE_SIZE}) @include(if: $withRefs) {{
    totalCount
    pageInfo {{ hasNextPage endCursor }}
    nodes {{ name target {{ ...HeadFields }} }}
  }}
}}
"""


def repo_alias(index: int) -> str:
    """The alias under which the ``index``-th repository of a batch is returned."""
    return f"r{index}"


def build_repo_batch_query(count: int) -> str:
    """One document that scans ``count`` repositories via aliased ``repository`` fields.

    Variables are ``$o<i>``/``$n<i>`` for each repository plus ``$withRefs``;
    see ``repo_batch_variables``. Batching cuts round-trips, which dominate
    the scan: a repository query costs GitHub about two rate-limit points
    but 300-800 ms of latency.
    """
    if count < 1:
        raise ValueError("A repository batch needs at least one repository.")
    declarations = ", ".join(f"$o{i}: String!, $n{i}: String!" for i in range(count))
    fields = "\n".join(
        f"  {repo_alias(i)}: repository(owner: $o{i}, name: $n{i}) {{ ...RepoFields }}"
        for i in range(count)
    )
    return (
        f"query RepoScan({declarations}, $withRefs: Boolean!) {{\n"
        f"  {_RATE_LIMIT_FIELD}\n{fields}\n}}\n"
        f"{_REPO_FRAGMENT}{_HEAD_FRAGMENT}"
    )


def rate_limit_of(data: dict[str, Any]) -> tuple[int, int | None]:
    """``(cost, remaining)`` from a document that selected ``rateLimit``; cost 0 when absent."""
    node = data.get("rateLimit")
    if not isinstance(node, dict):
        return 0, None
    cost = node.get("cost")
    remaining = node.get("remaining")
    return (cost if isinstance(cost, int) else 0, remaining if isinstance(remaining, int) else None)


class RateLimitTally:
    """Thread-safe sum of GraphQL points spent and the lowest remaining budget reported."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cost = 0
        self.remaining: int | None = None

    def add(self, cost: int, remaining: int | None) -> None:
        with self._lock:
            self.cost += cost
            if remaining is not None:
                self.remaining = (
                    remaining if self.remaining is None else min(self.remaining, remaining)
                )

    def record(self, data: dict[str, Any]) -> None:
        """Add the ``rateLimit`` node of one response."""
        self.add(*rate_limit_of(data))


def repo_batch_variables(repos: list[tuple[str, str]], *, with_refs: bool) -> dict[str, Any]:
    """Variables matching ``build_repo_batch_query(len(repos))``."""
    variables: dict[str, Any] = {"withRefs": with_refs}
    for index, (owner, name) in enumerate(repos):
        variables[f"o{index}"] = owner
        variables[f"n{index}"] = name
    return variables


REFS_PAGE_QUERY = f"""
query RefsPage($owner: String!, $name: String!, $after: String!) {{
  {_RATE_LIMIT_FIELD}
  repository(owner: $owner, name: $name) {{
    refs(refPrefix: "refs/heads/", first: {REFS_PAGE_SIZE}, after: $after) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{ name target {{ ...HeadFields }} }}
    }}
  }}
}}
{_HEAD_FRAGMENT}
"""

COMMIT_CHECKS_PAGE_QUERY = f"""
query CommitChecksPage($owner: String!, $name: String!, $oid: GitObjectID!, $after: String!) {{
  {_RATE_LIMIT_FIELD}
  repository(owner: $owner, name: $name) {{
    object(oid: $oid) {{
      ... on Commit {{
        statusCheckRollup {{
          contexts(first: {CONTEXTS_PAGE_SIZE}, after: $after) {{
            pageInfo {{ hasNextPage endCursor }}
{_CONTEXT_NODES}
          }}
        }}
      }}
    }}
  }}
}}
"""


def _error_message(errors: Any) -> str:
    if not isinstance(errors, list):
        return "GitHub GraphQL request failed."
    messages = [
        str(item.get("message") or "").strip()
        for item in errors
        if isinstance(item, dict) and item.get("message")
    ]
    return "; ".join(messages) or "GitHub GraphQL request failed."


def run_query_with_errors(
    client: GitHubRestClient,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    tally: RateLimitTally | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """POST one GraphQL document; return ``(data, errors)`` for partial results.

    Raises ``GitHubApiError`` when the transport fails, the response carries
    ``errors`` without any ``data``, or the payload is not an object. A
    batched query that lost one alias still returns the others in ``data``,
    with the per-alias failures in ``errors`` (each carries a ``path``).
    ``tally`` receives the response's ``rateLimit`` node when present.
    """
    payload = client.request(
        "POST", GRAPHQL_PATH, body={"query": query, "variables": variables or {}}
    )
    if not isinstance(payload, dict):
        raise GitHubApiError("GitHub GraphQL returned a non-object payload.", path=GRAPHQL_PATH)
    raw_errors = payload.get("errors")
    errors = [e for e in raw_errors if isinstance(e, dict)] if isinstance(raw_errors, list) else []
    data = payload.get("data")
    if not isinstance(data, dict):
        raise GitHubApiError(_error_message(errors), path=GRAPHQL_PATH)
    if tally is not None:
        tally.record(data)
    if all(value is None for key, value in data.items() if key != "rateLimit"):
        raise GitHubApiError(_error_message(errors), path=GRAPHQL_PATH)
    return data, errors


def run_query(
    client: GitHubRestClient,
    query: str,
    variables: dict[str, Any] | None = None,
    *,
    tally: RateLimitTally | None = None,
) -> dict[str, Any]:
    """POST one GraphQL document and return its ``data`` object."""
    data, _errors = run_query_with_errors(client, query, variables, tally=tally)
    return data


def error_for_path(errors: list[dict[str, Any]], root: str) -> str:
    """The message of the first error whose ``path`` starts at ``root``, or empty."""
    for item in errors:
        path = item.get("path")
        if isinstance(path, list) and path and path[0] == root:
            return str(item.get("message") or "").strip()
    return ""


__all__ = [
    "COMMIT_CHECKS_PAGE_QUERY",
    "GRAPHQL_PATH",
    "MAX_OPEN_PRS",
    "ORGS_PAGE_SIZE",
    "OWNER_REPOS_QUERY",
    "REFS_PAGE_QUERY",
    "REFS_PAGE_SIZE",
    "REPOS_PAGE_SIZE",
    "VIEWER_SCOPE_QUERY",
    "RateLimitTally",
    "build_repo_batch_query",
    "error_for_path",
    "rate_limit_of",
    "repo_alias",
    "repo_batch_variables",
    "run_query",
    "run_query_with_errors",
]
