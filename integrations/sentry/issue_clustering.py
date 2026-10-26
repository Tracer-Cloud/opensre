"""Structural clustering and human-readable labels for Sentry issue groups."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_TITLE_THEME_RE = re.compile(r"^\[([^\]]+)\]")
_CULPRIT_KEY_RE = re.compile(r"[^a-z0-9._-]+")

# Overrides where a generic package label would be wrong or too vague.
# Longest matching prefix wins (e.g. integrations.eks.* → EKS / Kubernetes).
STRUCTURAL_LABEL_OVERRIDES: dict[str, str] = {
    "integrations.eks": "EKS / Kubernetes errors",
    "integrations.cloudtrail": "CloudTrail / AWS errors",
    "core.llm": "LLM runtime / provider errors",
    "core.agent": "Agent runtime errors",
    "surfaces.cli": "CLI surface errors",
    "surfaces.interactive_shell": "Interactive shell errors",
    "infrastructure.harness_providers": "Harness / integration wiring errors",
    "uncategorised": "Uncategorised errors",
}


# Map repo package prefixes to cluster-key depth (shallow vs nested module path).
@dataclass(frozen=True)
class _ModuleClusterRule:
    prefix: str
    shallow_depth: int
    deep_depth: int
    min_dots_for_deep: int


_MODULE_CLUSTER_RULES: tuple[_ModuleClusterRule, ...] = (
    _ModuleClusterRule("integrations.", shallow_depth=2, deep_depth=3, min_dots_for_deep=2),
    _ModuleClusterRule("tools.", shallow_depth=2, deep_depth=3, min_dots_for_deep=2),
    _ModuleClusterRule("core.", shallow_depth=2, deep_depth=2, min_dots_for_deep=999),
    _ModuleClusterRule("surfaces.", shallow_depth=2, deep_depth=2, min_dots_for_deep=999),
    _ModuleClusterRule("infrastructure.", shallow_depth=2, deep_depth=2, min_dots_for_deep=999),
    _ModuleClusterRule("gateway.", shallow_depth=2, deep_depth=2, min_dots_for_deep=999),
)

# Package-style keys → label template ({name} = first path segment, title-cased).
_PACKAGE_LABEL_RULES: tuple[tuple[str, str], ...] = (
    ("integrations.", "{name} integration errors"),
    ("tools.", "{name} tool errors"),
    ("core.", "{name} runtime errors"),
    ("surfaces.", "{name} surface errors"),
    ("infrastructure.", "{name} infrastructure errors"),
)

# Fixed-prefix keys → label builder (remainder is the part after the prefix).
_SPECIAL_LABEL_RULES: tuple[tuple[str, Callable[[str], str]], ...] = (
    (
        "title-theme:",
        lambda rest: f"{rest.replace('_', ' ').title()} errors (from issue titles)",
    ),
    (
        "culprit:",
        lambda rest: f"Code path {rest.replace('_', '.')}",
    ),
    (
        "project:",
        lambda rest: f"Sentry project {rest} (fallback bucket — inspect samples)",
    ),
    (
        "issue-group:",
        lambda rest: f"Issue family {rest.upper()}",
    ),
)


def _culprit_module(culprit: str) -> str:
    text = culprit.strip()
    if " in " in text:
        return text.split(" in ", 1)[0].strip()
    return text


def _sanitize_key(text: str) -> str:
    cleaned = _CULPRIT_KEY_RE.sub("_", text.lower()).strip("._")
    return cleaned or "unknown"


def _package_cluster_key(module: str, *, depth: int) -> str:
    parts = [part for part in module.split(".") if part]
    if not parts:
        return "uncategorised"
    return ".".join(parts[:depth])


def _cluster_key_from_module(module: str) -> str | None:
    for rule in _MODULE_CLUSTER_RULES:
        if not module.startswith(rule.prefix):
            continue
        depth = (
            rule.deep_depth if module.count(".") >= rule.min_dots_for_deep else rule.shallow_depth
        )
        return _package_cluster_key(module, depth=depth)
    return None


def _title_theme_key(issue: dict[str, Any]) -> str | None:
    title = str(issue.get("title") or "").strip()
    match = _TITLE_THEME_RE.match(title)
    if not match:
        return None
    theme = _sanitize_key(match.group(1))
    return f"title-theme:{theme}" if theme != "unknown" else None


def _project_slug(issue: dict[str, Any]) -> str:
    project = issue.get("project")
    if isinstance(project, dict):
        return str(project.get("slug") or "").strip()
    if isinstance(project, str):
        return project.strip()
    return ""


def structural_cluster_key_for_issue(issue: dict[str, Any]) -> str:
    """Assign a stable structural bucket from culprit, title theme, or issue id."""
    module = _culprit_module(str(issue.get("culprit") or ""))

    module_key = _cluster_key_from_module(module)
    if module_key is not None:
        return module_key

    if module and "." in module:
        return f"culprit:{_sanitize_key(module)}"

    title_theme = _title_theme_key(issue)
    if title_theme is not None:
        return title_theme

    short_id = str(issue.get("shortId") or "")
    if "-" in short_id:
        return f"issue-group:{short_id.rsplit('-', 1)[0].lower()}"

    slug = _project_slug(issue)
    if slug:
        return f"project:{slug}"

    if module:
        return f"culprit:{_sanitize_key(module)}"

    return "uncategorised"


def _truncate(text: str, limit: int) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 1]}…"


def _package_title(package: str) -> str:
    return package.replace("_", " ").title()


def _structural_label_override(key: str) -> str | None:
    if key in STRUCTURAL_LABEL_OVERRIDES:
        return STRUCTURAL_LABEL_OVERRIDES[key]
    best_label: str | None = None
    best_prefix_len = -1
    for prefix, label in STRUCTURAL_LABEL_OVERRIDES.items():
        if (key == prefix or key.startswith(f"{prefix}.")) and len(prefix) > best_prefix_len:
            best_label = label
            best_prefix_len = len(prefix)
    return best_label


def _label_from_package_rules(key: str) -> str | None:
    for prefix, template in _PACKAGE_LABEL_RULES:
        if not key.startswith(prefix):
            continue
        package = key.removeprefix(prefix).split(".", 1)[0]
        return template.format(name=_package_title(package))
    return None


def _label_from_special_rules(key: str) -> str | None:
    for prefix, build_label in _SPECIAL_LABEL_RULES:
        if key.startswith(prefix):
            return build_label(key.removeprefix(prefix))
    return None


def _generic_structural_label(key: str) -> str:
    return _label_from_package_rules(key) or _label_from_special_rules(key) or key


def structural_cluster_label(key: str, *, sample_titles: tuple[str, ...] = ()) -> str:
    """Map a structural cluster key to a human-readable label for summaries."""
    base = _structural_label_override(key) or _generic_structural_label(key)
    if sample_titles:
        return f"{base} — e.g. {_truncate(sample_titles[0], 72)}"
    return base
