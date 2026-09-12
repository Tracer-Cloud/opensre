"""Canonical skill slugs and the legacy names persisted schedules may still carry."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

# Pre-gerund names that shipped in starter loops and user-confirmed schedules.
# A persisted task keeps the old string forever, so lookups map it forward here.
LEGACY_SKILL_NAMES: Mapping[str, str] = MappingProxyType(
    {
        "morning-report": "delivering-morning-briefings",
        "github-ci-health": "reporting-github-ci-failures",
        "github-ci-fix": "fixing-github-ci",
        "github-security-fix": "fixing-github-security-alerts",
        # Onboarding tree, renamed to <verb-ing>-<object> in one change.
        "onboarding-cicd-fix": "onboarding-github-ci",
        "cicd-analytics-demo": "analyzing-github-ci-performance",
        "cicd-reliability-agent": "scheduling-github-ci-fixes",
        "slack-handoff": "connecting-slack",
        "onboarding-analyzing-github-ci-performance": "analyzing-github-ci-performance",
        "onboarding-scheduling-github-ci-fixes": "scheduling-github-ci-fixes",
        "onboarding-connecting-slack": "connecting-slack",
    }
)


def normalize_skill_name(name: str) -> str:
    """Return the canonical kebab-case slug, mapping legacy names to their successor."""
    slug = name.strip().lower().replace("_", "-")
    return LEGACY_SKILL_NAMES.get(slug, slug)


def is_legacy_skill_name(name: str) -> bool:
    """True when ``name`` is a retired slug that :func:`normalize_skill_name` maps forward."""
    return name.strip().lower().replace("_", "-") in LEGACY_SKILL_NAMES


__all__ = ["LEGACY_SKILL_NAMES", "is_legacy_skill_name", "normalize_skill_name"]
