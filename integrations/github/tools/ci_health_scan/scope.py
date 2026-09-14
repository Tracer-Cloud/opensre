"""Resolve which repositories a CI health scan covers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from integrations.github.client import GitHubApiError, GitHubRestClient
from integrations.github.tools.ci_health_scan.graphql import (
    ORGS_PAGE_SIZE,
    OWNER_REPOS_QUERY,
    REPOS_PAGE_SIZE,
    VIEWER_SCOPE_QUERY,
    RateLimitTally,
    run_query,
)
from integrations.github.tools.ci_health_scan.models import RepoRef

#: Hard stop on repository pages per owner (100 repos each).
MAX_REPO_PAGES_PER_OWNER = 20
#: Hard stop on organization pages for the viewer (100 organizations each).
MAX_ORG_PAGES = 10
_PRIVACY_BY_VISIBILITY = {"all": None, "private": "PRIVATE", "public": "PUBLIC"}


@dataclass(frozen=True, slots=True)
class ScopeResolution:
    """The repositories to scan plus what was left out and why."""

    owners: tuple[str, ...]
    repos: tuple[RepoRef, ...]
    skipped_stale: int
    coverage_notices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _OwnerListing:
    """One owner's repositories, or the error that kept them from being listed."""

    repos: tuple[RepoRef, ...] = ()
    skipped: int = 0
    notices: tuple[str, ...] = ()
    error: GitHubApiError | None = None


def _parse_time(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _next_cursor(page: Any) -> str | None:
    """The cursor of the following page, or ``None`` when this page is the last."""
    info = page.get("pageInfo") if isinstance(page, dict) else None
    if not (isinstance(info, dict) and info.get("hasNextPage")):
        return None
    return str(info.get("endCursor") or "") or None


def resolve_viewer_owners(
    client: GitHubRestClient, *, tally: RateLimitTally | None = None
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(owners, notices)``: the token's login, then every organization it belongs to."""
    owners: list[str] = []
    notices: list[str] = []
    after: str | None = None
    for _page in range(MAX_ORG_PAGES):
        data = run_query(client, VIEWER_SCOPE_QUERY, {"after": after}, tally=tally)
        viewer = data.get("viewer")
        if not isinstance(viewer, dict):
            break
        login = str(viewer.get("login") or "").strip()
        if login and login not in owners:
            owners.append(login)
        orgs = viewer.get("organizations")
        nodes = orgs.get("nodes") if isinstance(orgs, dict) else None
        if isinstance(nodes, list):
            for org in nodes:
                org_login = str((org or {}).get("login") or "").strip()
                if org_login and org_login not in owners:
                    owners.append(org_login)
        after = _next_cursor(orgs)
        if after is None:
            break
    else:
        notices.append(
            f"Coverage notice: the token belongs to more than {MAX_ORG_PAGES * ORGS_PAGE_SIZE} "
            "organizations; only the first were scanned. Pass owners to reach the rest."
        )
    return tuple(owners), tuple(notices)


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
    tally: RateLimitTally | None,
) -> _OwnerListing:
    """Repos for one owner, newest push first, stopping at the staleness cutoff.

    An owner GitHub cannot resolve (typo, no access, exhausted budget) becomes
    a listing with ``error`` set, so one bad owner never aborts the others.
    """
    repos: list[RepoRef] = []
    notices: list[str] = []
    skipped = 0
    after: str | None = None
    for _page in range(MAX_REPO_PAGES_PER_OWNER):
        try:
            data = run_query(
                client,
                OWNER_REPOS_QUERY,
                {"login": owner, "after": after, "privacy": privacy},
                tally=tally,
            )
        except GitHubApiError as exc:
            notices.append(f"Coverage notice: owner {owner} could not be listed ({exc}).")
            return _OwnerListing(tuple(repos), skipped, tuple(notices), error=exc)
        holder = data.get("repositoryOwner")
        if not isinstance(holder, dict):
            notices.append(f"Coverage notice: owner {owner} was not found or is not accessible.")
            return _OwnerListing(tuple(repos), skipped, tuple(notices))
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
        after = _next_cursor(page)
        if after is None:
            break
    else:
        notices.append(
            f"Coverage notice: {owner} has more than "
            f"{MAX_REPO_PAGES_PER_OWNER * REPOS_PAGE_SIZE} repositories; "
            "only the most recently pushed were scanned."
        )
    return _OwnerListing(tuple(repos), skipped, tuple(notices))


def resolve_scope(
    client: GitHubRestClient,
    *,
    owners: list[str] | None,
    visibility: str = "all",
    since_days: int | None = None,
    now: datetime | None = None,
    concurrency: int = 4,
    tally: RateLimitTally | None = None,
) -> ScopeResolution:
    """Turn owner names (or the token's whole reach) into the repositories to scan.

    Raises ``GitHubApiError`` only when nothing could be listed at all (the
    viewer lookup failed, or every owner failed the same way); a subset of
    unreadable owners is reported through ``coverage_notices`` instead.
    """
    notices: list[str] = []
    resolved_owners = tuple(dict.fromkeys(o.strip() for o in (owners or []) if o and o.strip()))
    if not resolved_owners:
        resolved_owners, viewer_notices = resolve_viewer_owners(client, tally=tally)
        notices.extend(viewer_notices)
    privacy = _PRIVACY_BY_VISIBILITY.get(visibility)
    cutoff: datetime | None = None
    if since_days is not None and since_days > 0:
        cutoff = (now or datetime.now(UTC)) - timedelta(days=since_days)

    def fetch(owner: str) -> _OwnerListing:
        return _owner_repositories(client, owner, privacy=privacy, cutoff=cutoff, tally=tally)

    repos: list[RepoRef] = []
    skipped = 0
    failures: list[GitHubApiError] = []
    if resolved_owners:
        workers = max(1, min(concurrency, len(resolved_owners)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for listing in pool.map(fetch, resolved_owners):
                repos.extend(listing.repos)
                skipped += listing.skipped
                notices.extend(listing.notices)
                if listing.error is not None:
                    failures.append(listing.error)
        if len(failures) == len(resolved_owners):
            raise failures[0]
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


__all__ = [
    "MAX_ORG_PAGES",
    "MAX_REPO_PAGES_PER_OWNER",
    "ScopeResolution",
    "resolve_scope",
    "resolve_viewer_owners",
]
