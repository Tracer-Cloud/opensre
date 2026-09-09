"""Record onboarding menu outcomes without collecting custom answer text."""

from __future__ import annotations

import logging
from types import MappingProxyType

from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.spi.grounding import getting_started_skills
from infrastructure.analytics.capture import (
    capture_onboarding_demo_selected,
    capture_onboarding_demo_skipped,
)

logger = logging.getLogger(__name__)

# Preserve the identifiers used by the original startup picker.
_OPTION_BY_SKILL = MappingProxyType(
    {
        "cicd-analytics-demo": "ci_analytics",
        "cicd-reliability-agent": "ci_agent",
        "slack-handoff": "slack",
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
