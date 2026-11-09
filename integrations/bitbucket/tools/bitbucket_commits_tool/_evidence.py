"""Evidence mapper for list_bitbucket_commits."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry


def map_list_bitbucket_commits(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the commit count and repo, qualifying page-capped totals.

    ``effective_limit`` is the real ceiling ``list_commits`` applied --
    ``min(limit, config.max_results)`` -- so a returned count at that ceiling
    may understate the true number of matching commits.
    """
    if not output.get("available"):
        return
    commits = output.get("commits") or []
    if not commits:
        return
    total = output.get("total_returned", len(commits))
    effective_limit = output.get("effective_limit", total)
    count_label = f"{total}+" if total >= effective_limit else str(total)
    summary = f"{count_label} commit(s)"
    repo = output.get("repo")
    if repo:
        summary += f" for '{repo}'"
    record_evidence_entry(
        evidence,
        source="list_bitbucket_commits",
        label="Bitbucket Commits",
        summary=summary,
    )
