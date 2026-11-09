"""Whether this process may pull and activate published skills releases."""

from __future__ import annotations

import os
import sys

from config.constants.skills import SKILLS_AUTO_UPDATE_ENV

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def skills_auto_update_enabled() -> bool:
    """``OPENSRE_SKILLS_AUTO_UPDATE`` when set, else on for release binaries only.

    Source checkouts and tests stay on the cards in the working tree unless
    they opt in, so a developer's edits are never shadowed by a release.
    """
    raw = os.getenv(SKILLS_AUTO_UPDATE_ENV, "").strip().lower()
    if raw in _TRUE:
        return True
    if raw in _FALSE:
        return False
    return bool(getattr(sys, "frozen", False))


__all__ = ["skills_auto_update_enabled"]
