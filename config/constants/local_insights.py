"""Vocabulary of the local repository insights: kinds, report labels, and why the analysis ran.

The tool names each insight by kind and label; the report leads each bullet with
the label, and the value recorder maps that label back to its kind, so a
delivered insight is recorded without repository names.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class LocalInsightKind(StrEnum):
    """One kind of finding, in the order the report leads with them."""

    RETIRED_ACTIONS = "retired_actions"
    FOLLOW_UP_FIXES = "follow_up_fixes"
    CI_TRIAL_AND_ERROR = "ci_trial_and_error"
    AI_PAIRING = "ai_pairing"
    WORKFLOW_HYGIENE = "workflow_hygiene"
    NO_CI = "no_ci"
    TESTS_WITH_CODE = "tests_with_code"
    HOOKS_NOT_INSTALLED = "hooks_not_installed"
    REVERTS = "reverts"
    LOOSE_ENDS = "loose_ends"
    LARGE_COMMITS = "large_commits"
    OTHER_CI = "other_ci"
    RHYTHM = "rhythm"


#: Bold label that starts the report bullet of each kind.
LOCAL_INSIGHT_LABELS: Mapping[LocalInsightKind, str] = MappingProxyType(
    {
        LocalInsightKind.RETIRED_ACTIONS: "Retired actions",
        LocalInsightKind.FOLLOW_UP_FIXES: "Follow-up fixes",
        LocalInsightKind.CI_TRIAL_AND_ERROR: "CI trial and error",
        LocalInsightKind.AI_PAIRING: "AI pairing",
        LocalInsightKind.WORKFLOW_HYGIENE: "Workflow hygiene",
        LocalInsightKind.NO_CI: "No CI",
        LocalInsightKind.TESTS_WITH_CODE: "Tests with code",
        LocalInsightKind.HOOKS_NOT_INSTALLED: "Local checks",
        LocalInsightKind.REVERTS: "Reverts",
        LocalInsightKind.LOOSE_ENDS: "Loose ends",
        LocalInsightKind.LARGE_COMMITS: "Large commits",
        LocalInsightKind.OTHER_CI: "Other CI",
        LocalInsightKind.RHYTHM: "Rhythm",
    }
)


class LocalInsightsReason(StrEnum):
    """Why the local analysis ran instead of, or before, the GitHub CI report."""

    GITHUB_NOT_CONNECTED = "github_not_connected"
    GITHUB_FAILED = "github_failed"
    NO_GITHUB_ACTIONS = "no_github_actions"
    REQUESTED = "requested"


#: What the GitHub CI report adds, named in the local report as what connecting GitHub unlocks.
GITHUB_ONLY_METRICS: tuple[str, ...] = ("CI waiting time", "PR failure rate", "Red time on main")

#: The ``error_kind`` values the GitHub CI analysis reports (``GitHubFailureKind``). Analytics
#: records one of these or ``other``, never the text a caller passed.
GITHUB_ERROR_KINDS: frozenset[str] = frozenset(
    {
        "rate_limited",
        "unauthorized",
        "not_found",
        "tls_untrusted",
        "unreachable",
        "server_error",
        "invalid_response",
        "other",
    }
)

__all__ = [
    "GITHUB_ERROR_KINDS",
    "GITHUB_ONLY_METRICS",
    "LOCAL_INSIGHT_LABELS",
    "LocalInsightKind",
    "LocalInsightsReason",
]
