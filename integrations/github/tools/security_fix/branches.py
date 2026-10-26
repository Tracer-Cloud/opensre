"""Branch names of OpenSRE security fixes, shared by finding selection and shipping."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

FIX_BRANCH_PREFIX = "opensre/github-security-fix"


def _slug(value: object) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "")).strip("-")
    return cleaned or "alert"


def fix_branch_stem(alert_type: str, number: object) -> str:
    """Prefix of every fix branch for one finding; the trailing ``-`` keeps #5 apart from #50."""
    return f"{FIX_BRANCH_PREFIX}-{_slug(alert_type)}-{_slug(number)}-"


def open_fix_branches(pulls: Iterable[Any], *, repository: str) -> frozenset[str]:
    """Head branches of open OpenSRE fix PRs pushed to ``repository`` itself.

    A fork can name its branch like ours, so only same-repository heads count.
    """
    branches: set[str] = set()
    for pull in pulls:
        head = pull.get("head") if isinstance(pull, dict) else None
        if not isinstance(head, dict):
            continue
        head_repo = head.get("repo")
        full_name = str(head_repo.get("full_name") or "") if isinstance(head_repo, dict) else ""
        ref = str(head.get("ref") or "")
        if full_name.casefold() == repository.casefold() and ref.startswith(FIX_BRANCH_PREFIX):
            branches.add(ref)
    return frozenset(branches)


__all__ = ["FIX_BRANCH_PREFIX", "fix_branch_stem", "open_fix_branches"]
