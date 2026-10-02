"""Report skills catalog switches to product analytics, once per machine and release."""

from __future__ import annotations

import threading

from core.agent_harness.spi.skill_releases import (
    SkillCatalogSnapshot,
    SkillSource,
    active_skill_catalog,
    read_announced,
    write_announced,
)
from infrastructure.analytics.capture import capture_skills_release_activated

_installed = False
_installed_lock = threading.Lock()


def _announce(new: SkillCatalogSnapshot, previous: SkillCatalogSnapshot | None) -> None:
    # A fresh process on the bundled cards is not news; a release reaching this
    # machine (or a switch inside a running process) is, once per release.
    if previous is None and new.source is not SkillSource.REMOTE:
        return
    if read_announced() == new.release:
        return
    write_announced(new.release)
    capture_skills_release_activated(
        skills_release=new.release,
        skills_source=str(new.source),
        previous_release=previous.release if previous is not None else None,
        skill_count=len(new.skills),
    )


def install_skills_activation_telemetry() -> None:
    """Register the activation listener once per process."""
    global _installed
    with _installed_lock:
        if _installed:
            return
        active_skill_catalog().add_listener(_announce)
        _installed = True


__all__ = ["install_skills_activation_telemetry"]
