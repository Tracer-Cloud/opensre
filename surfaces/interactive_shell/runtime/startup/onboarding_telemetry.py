"""Record onboarding menu outcomes without collecting custom answer text."""

from __future__ import annotations

import logging
from types import MappingProxyType

from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    CONNECTING_SLACK_SKILL_NAME,
    DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME,
    ONBOARDING_LEAF_CHOICES,
    ONBOARDING_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)
from core.agent_harness.spi.grounding import getting_started_skills
from infrastructure.analytics.capture import (
    capture_onboarding_demo_selected,
    capture_onboarding_demo_skipped,
)

logger = logging.getLogger(__name__)

# Outcome-menu labels map to the same skill ids as the children's getting_started text.
_SKILL_BY_LEAF_LABEL = MappingProxyType({label: name for name, label in ONBOARDING_LEAF_CHOICES})

# Telemetry option ids are stable across skill renames (dashboards key on them).
_OPTION_BY_SKILL = MappingProxyType(
    {
        ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME: "ci_analytics",
        SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME: "ci_agent",
        DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME: "remote_managed_service",
        CONNECTING_SLACK_SKILL_NAME: "slack",
    }
)


def capture_onboarding_choice(
    skill_name: str | None, selected: str | None, *, custom: bool
) -> None:
    """Capture only the master menu's outcome; telemetry never blocks continuation."""
    if skill_name != ONBOARDING_SKILL_NAME:
        return
    try:
        if selected is None:
            capture_onboarding_demo_skipped()
            return
        if custom:
            capture_onboarding_demo_selected(option="custom", custom=True)
            return
        leaf_skill = _SKILL_BY_LEAF_LABEL.get(selected)
        if leaf_skill is not None:
            option = _OPTION_BY_SKILL.get(leaf_skill, leaf_skill.replace("-", "_"))
            capture_onboarding_demo_selected(option=option, custom=False)
            return
        option = next(
            (
                _OPTION_BY_SKILL.get(skill.name, skill.name.replace("-", "_"))
                for skill in getting_started_skills()
                if skill.getting_started == selected
            ),
            "custom",
        )
        capture_onboarding_demo_selected(option=option, custom=option == "custom")
    except Exception:
        logger.debug("Could not capture onboarding outcome.", exc_info=True)
