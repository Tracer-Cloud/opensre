"""Resolve which repositories a CI health scan covers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from integrations.github.client import GitHubRestClient
from integrations.github.tools.ci_health_scan.graphql import (
    OWNER_REPOS_QUERY,
    VIEWER_SCOPE_QUERY,
    run_query,
)
from integrations.github.tools.ci_health_scan.models import RepoRef

#: Hard stop on repository pages per owner (100 repos each).
MAX_REPO_PAGES_PER_OWNER = 20
_PRIVACY_BY_VISIBILITY = {"all": None, "private": "PRIVATE", "public": "PUBLIC"}


@dataclass(frozen=True, slots=True)
class ScopeResolution:
    """The repositories to scan plus what was left out and why."""

    owners: tuple[str, ...]
    repos: tuple[RepoRef, ...]
    skipped_stale: int
    coverage_notices: tuple[str, ...]


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def resolve_viewer_owners(client: GitHubRestClient) -> tuple[str, ...]:
    """The token's own login followed by every organization it belongs to."""
    data = run_query(client, VIEWER_SCOPE_QUERY)
    viewer = data.get("viewer")
    if not isinstance(viewer, dict):
        return ()
    owners: list[str] = []
    login = str(viewer.get("login") or "").strip()
    if login:
        owners.append(login)
    orgs = (viewer.get("organizations") or {}).get("nodes")
    if isinstance(orgs, list):
        for org in orgs:
            org_login = str((org or {}).get("login") or "").strip()
            if org_login and org_login not in owners:
                owners.append(org_login)
    return tuple(owners)


def _repo_ref(node: Any, *, fallback_owner: str) -> RepoRef | None:
    if not isinstance(node, dict):
        return None
    name = str(node.get("name") or "").strip()
    if not name:
        return None
    owner = str(((node.get("owner") or {}).get("login")) or fallback_owner).strip()
    return RepoRef(
        owner=owner,
        name=name,
        is_private=bool(node.get("isPrivate")),
        pushed_at=str(node.get("pushedAt") or ""),
    )


def _owner_repositories(
    client: GitHubRestClient,
    owner: str,
    *,
    privacy: str | None,
    cutoff: datetime | None,
) -> tuple[list[RepoRef], int, list[str]]:
    """Repos for one owner, newest push first, stopping at the staleness cutoff."""
    repos: list[RepoRef] = []
    notices: list[str] = []
    skipped = 0
    after: str | None = None
    for _page in range(MAX_REPO_PAGES_PER_OWNER):
        data = run_query(
            client, OWNER_REPOS_QUERY, {"login": owner, "after": after, "privacy": privacy}
        )
        holder = data.get("repositoryOwner")
        if not isinstance(holder, dict):
            notices.append(f"Coverage notice: owner {owner} was not found or is not accessible.")
            return repos, skipped, notices
        page = holder.get("repositories") or {}
        nodes = page.get("nodes") if isinstance(page, dict) else None
        if not isinstance(nodes, list):
            break
        for node in nodes:
            ref = _repo_ref(node, fallback_owner=owner)
            if ref is None:
                continue
            pushed = _parse_time(ref.pushed_at)
            if cutoff is not None and pushed is not None and pushed < cutoff:
                # Ordered by push date: everything after this is older still.
                skipped += 1
                continue
            repos.append(ref)
        if skipped:
            # The stale tail counts what we saw on this page; remaining pages
            # are only older, so stop paginating.
            break
        info = page.get("pageInfo") if isinstance(page, dict) else None
        if not (isinstance(info, dict) and info.get("hasNextPage")):
            break
        after = str(info.get("endCursor") or "") or None
        if after is None:
            break
    else:
        notices.append(
            f"Coverage notice: {owner} has more than "
            f"{MAX_REPO_PAGES_PER_OWNER * 100} repositories; only the most recently pushed were scanned."
        )
    return repos, skipped, notices


def resolve_scope(
    client: GitHubRestClient,
    *,
    owners: list[str] | None,
    visibility: str = "all",
    since_days: int | None = None,
    now: datetime | None = None,
    concurrency: int = 4,
) -> ScopeResolution:
    """Turn owner names (or the token's whole reach) into the repositories to scan."""
    resolved_owners = tuple(dict.fromkeys(o.strip() for o in (owners or []) if o and o.strip()))
    if not resolved_owners:
        resolved_owners = resolve_viewer_owners(client)
    privacy = _PRIVACY_BY_VISIBILITY.get(visibility)
    cutoff: datetime | None = None
    if since_days is not None and since_days > 0:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=since_days)

    def fetch(owner: str) -> tuple[list[RepoRef], int, list[str]]:
        return _owner_repositories(client, owner, privacy=privacy, cutoff=cutoff)

    repos: list[RepoRef] = []
    skipped = 0
    notices: list[str] = []
    if resolved_owners:
        workers = max(1, min(concurrency, len(resolved_owners)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for owner_repos, owner_skipped, owner_notices in pool.map(fetch, resolved_owners):
                repos.extend(owner_repos)
                skipped += owner_skipped
                notices.extend(owner_notices)
    if skipped:
        window = since_days if since_days is not None else 0
        notices.append(
            f"Coverage notice: at least {skipped} repositories with no push in the last "
            f"{window} days were skipped; pass since_days=0 to include them."
        )
    seen: set[str] = set()
    unique: list[RepoRef] = []
    for ref in repos:
        if ref.full_name not in seen:
            seen.add(ref.full_name)
            unique.append(ref)
    return ScopeResolution(
        owners=resolved_owners,
        repos=tuple(unique),
        skipped_stale=skipped,
        coverage_notices=tuple(notices),
    )


__all__ = ["MAX_REPO_PAGES_PER_OWNER", "ScopeResolution", "resolve_scope", "resolve_viewer_owners"]
