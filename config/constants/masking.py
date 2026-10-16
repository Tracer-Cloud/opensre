"""Masking feature env-var names.

Single source of truth so policy, tests, and any future surface import
one canonical name rather than duplicating the literal string.
"""

from __future__ import annotations

from typing import Final

OPENSRE_MASK_ENABLED_ENV: Final[str] = "OPENSRE_MASK_ENABLED"
OPENSRE_MASK_KINDS_ENV: Final[str] = "OPENSRE_MASK_KINDS"
OPENSRE_MASK_EXTRA_REGEX_ENV: Final[str] = "OPENSRE_MASK_EXTRA_REGEX"
