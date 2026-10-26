"""CI reliability analyses started before the model calls ``analyze_github_ci_reliability``."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from config.constants.tooling import (
    CI_ANALYSIS_PREFETCH_MAX_AGE_SECONDS,
    TOOL_PREFETCH_MAX_ENTRIES,
)
from core.tool_framework.utils import PrefetchRegistry
from integrations.github.tools.ci_analytics.analysis import Analysis


@dataclass(frozen=True)
class AnalysisRequest:
    """One live read's arguments; a prefetch answers only a call with the same ones.

    The token is kept as a digest, so a call resolving another token reads live.
    """

    owner: str
    repo: str
    days: int
    token_digest: str


@dataclass(frozen=True)
class PrefetchedAnalysis:
    """A live analysis read in the background, and the instant its window ends."""

    analysis: Analysis
    now: datetime


_ANALYSES: PrefetchRegistry[AnalysisRequest, PrefetchedAnalysis] = PrefetchRegistry(
    name="github-ci-analysis",
    max_age_seconds=CI_ANALYSIS_PREFETCH_MAX_AGE_SECONDS,
    max_entries=TOOL_PREFETCH_MAX_ENTRIES,
)


def analysis_request(owner: str, repo: str, *, days: int, token: str) -> AnalysisRequest:
    """The key of an analysis of ``owner/repo`` over ``days`` read with ``token``."""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return AnalysisRequest(owner=owner, repo=repo, days=days, token_digest=digest)


def start_analysis_prefetch(
    request: AnalysisRequest, work: Callable[[], PrefetchedAnalysis]
) -> bool:
    """Run ``work`` in the background for ``request``; False when one is already kept."""
    return _ANALYSES.start(request, lambda _should_stop: work())


def claim_analysis_prefetch(request: AnalysisRequest) -> PrefetchedAnalysis | None:
    """The prefetched analysis for ``request``, or None when the tool must read GitHub itself."""
    return _ANALYSES.claim(request)


def reset_analysis_prefetch() -> None:
    """Forget every prefetched analysis (test isolation)."""
    _ANALYSES.reset()


__all__ = [
    "AnalysisRequest",
    "PrefetchedAnalysis",
    "analysis_request",
    "claim_analysis_prefetch",
    "reset_analysis_prefetch",
    "start_analysis_prefetch",
]
