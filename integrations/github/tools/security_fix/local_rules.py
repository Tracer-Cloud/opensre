"""Builtin local fixer rule mapping for GitHub Code Quality findings."""

from __future__ import annotations

_UNUSED_IMPORT_MARKERS = frozenset({"py/unused-import", "unused import", "f401"})
_UNUSED_LOCAL_MARKERS = frozenset({"py/unused-local-variable", "unused local variable", "f841"})
_BUILTIN_ALERT_TYPES = frozenset({"code_quality", "code_scanning"})


def local_ruff_codes(*, rule_id: str, title: str) -> tuple[str, ...]:
    """Return Ruff rule codes that can safely handle a Code Quality finding."""
    normalized = {rule_id.strip().lower(), title.strip().lower()}
    if normalized & _UNUSED_IMPORT_MARKERS:
        return ("F401",)
    if normalized & _UNUSED_LOCAL_MARKERS:
        return ("F841",)
    return ()


def has_builtin_local_fix(*, alert_type: str, rule_id: str, title: str) -> bool:
    """Whether OpenSRE has a deterministic local fixer for the finding."""
    return alert_type in _BUILTIN_ALERT_TYPES and bool(
        local_ruff_codes(rule_id=rule_id, title=title)
    )


__all__ = ["has_builtin_local_fix", "local_ruff_codes"]
