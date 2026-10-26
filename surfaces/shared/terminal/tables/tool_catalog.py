"""Tool-registry catalog generator for the interactive shell.

The OpenSRE tool registry (:mod:`tools.registry`) auto-discovers tools
under ``tools/`` and exposes them via :func:`get_registered_tools`. Each
:class:`~tools.registered_tool.RegisteredTool` carries rich metadata —
``name``, ``description``, ``surfaces``, ``input_schema``, ``source``, and
the module it was discovered in — but none of this is currently visible to
the interactive-shell user.

This module turns the registry snapshot into a compact catalog suitable for
two independent consumers:

1. The ``/tools`` slash command — users can see what tools are wired
   into their build of the shell.
2. Future codebase-aware grounding — the catalog text can be injected into
   the LLM prompt so the assistant can answer "what tools can the chat
   agent call?" accurately. (Wiring lives in a separate later issue;
   ``format_tool_catalog_text`` is shaped to support both surfaces today.)

Two pure functions:

- :func:`build_tool_catalog` — wraps :func:`get_registered_tools` and returns
  :class:`ToolCatalogEntry` records with the fields needed for display and
  prompt injection.
- :func:`format_tool_catalog_text` — renders entries as compact Markdown-ish
  text grouped by surface, suitable for both terminal display and LLM
  grounding.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.constants.paths import REPO_ROOT
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool
from tools.registry import get_registered_tools

# Cap one-line schema summaries so wide registries don't break terminal
# wrapping or balloon prompt injection. The cap is generous — tools with
# many params get a trailing ellipsis rather than an unwieldy line.
_MAX_SCHEMA_SUMMARY_CHARS = 200


@dataclass(frozen=True)
class ToolParameter:
    """A top-level parameter with its declared type and requiredness."""

    name: str
    type_label: str
    required: bool


@dataclass(frozen=True)
class ToolCatalogEntry:
    """Display-shaped projection of a :class:`RegisteredTool`."""

    name: str
    """Registered tool name, e.g. ``"search_github_code"``."""

    surfaces: tuple[str, ...]
    """Surfaces the tool is exposed on (``"chat"`` and/or ``"action"``)."""

    description: str
    """One-line description from the tool's metadata, trimmed for display."""

    source_file: str
    """Repo-relative path (forward slashes) when ``__file__`` lives under the
    checkout root, otherwise the resolved absolute POSIX path when the defining
    module is outside the repo (e.g. an installed editable or site-packages
    shim). Empty string only when ``origin_module`` is missing, import fails,
    or the loaded module exposes no ``__file__``."""

    input_schema_summary: str
    """One-line render of top-level params, e.g.
    ``"query: string, limit?: integer"``. ``"(no params)"`` when the tool
    takes no inputs."""

    parameters: tuple[ToolParameter, ...] = ()
    """Unabridged parameters for the interactive detail view."""


def _resolve_source_file(tool: RegisteredTool) -> str:
    """Best-effort relative path to the tool's defining file.

    The tool registry stores ``origin_module`` (a dotted module path); we
    import it to read ``__file__``. Failures here are non-fatal — the
    catalog still surfaces the tool, just without a source pointer.
    """
    module_name = tool.origin_module
    if not module_name:
        return ""
    try:
        module = importlib.import_module(module_name)
    except Exception:
        return ""
    file_attr = getattr(module, "__file__", None)
    if not file_attr:
        return ""
    try:
        return Path(file_attr).resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        # Module lives outside the repo root (installed package, namespace
        # package). Fall back to the absolute path so users can still open
        # it; ``ValueError`` here means ``relative_to`` couldn't compute a
        # relative path, not that the file is missing.
        return Path(file_attr).as_posix()


def _schema_parameters(input_schema: dict[str, Any]) -> tuple[ToolParameter, ...]:
    properties = input_schema.get("properties") or {}
    required = set(input_schema.get("required") or ())
    return tuple(
        ToolParameter(
            name,
            str(info.get("type") or "any") if isinstance(info, dict) else "any",
            name in required,
        )
        for name, info in properties.items()
    )


def _summarize_input_schema(input_schema: dict[str, Any]) -> str:
    """Render top-level params as a one-line ``name: type`` list.

    Required params render as ``name: type``; optional params get a ``?``
    suffix (``name?: type``). Untyped properties (no ``type`` key) render
    as ``any`` so the user can see the param exists. Returns
    ``"(no params)"`` for empty schemas.
    """
    parameters = _schema_parameters(input_schema)
    if not parameters:
        return "(no params)"
    rendered = ", ".join(
        f"{param.name}{'' if param.required else '?'}: {param.type_label}" for param in parameters
    )
    if len(rendered) > _MAX_SCHEMA_SUMMARY_CHARS:
        return rendered[: _MAX_SCHEMA_SUMMARY_CHARS - 1].rstrip(", ") + "…"
    return rendered


def _entry_from_tool(tool: RegisteredTool) -> ToolCatalogEntry:
    return ToolCatalogEntry(
        name=tool.name,
        surfaces=tuple(tool.surfaces),
        description=(tool.description or "").strip(),
        source_file=_resolve_source_file(tool),
        input_schema_summary=_summarize_input_schema(tool.input_schema),
        parameters=_schema_parameters(tool.input_schema),
    )


def build_tool_catalog(surface: ToolSurface | None = None) -> list[ToolCatalogEntry]:
    """Return :class:`ToolCatalogEntry` records for tools registered on ``surface``.

    Pass ``surface=None`` (default) to get every registered tool, or
    ``"chat"`` / ``"action"`` to filter. The order matches
    :func:`get_registered_tools` (alphabetical by tool name).
    """
    return [_entry_from_tool(tool) for tool in get_registered_tools(surface)]


def _surface_sort_key(surface: str) -> tuple[int, str]:
    """Stable surface ordering — chat first, others alphabetical."""
    priority = {"chat": 0}
    return (priority.get(surface, 99), surface)


def format_tool_catalog_text(entries: list[ToolCatalogEntry]) -> str:
    """Render entries as compact Markdown-ish text grouped by surface.

    Each tool may belong to multiple surfaces and will appear under each one
    so the user can see at a glance which tools each surface can reach.
    Returns ``""`` for an empty catalog.
    """
    if not entries:
        return ""

    by_surface: dict[str, list[ToolCatalogEntry]] = {}
    for entry in entries:
        for surface in entry.surfaces:
            by_surface.setdefault(surface, []).append(entry)

    lines: list[str] = []
    for surface in sorted(by_surface.keys(), key=_surface_sort_key):
        bucket = by_surface[surface]
        lines.append(f"## {surface} ({len(bucket)} tool{'s' if len(bucket) != 1 else ''})")
        lines.append("")
        for entry in bucket:
            lines.append(f"- **{entry.name}** — {entry.description}")
            if entry.source_file:
                lines.append(f"  - source: `{entry.source_file}`")
            lines.append(f"  - params: `{entry.input_schema_summary}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "ToolCatalogEntry",
    "ToolParameter",
    "build_tool_catalog",
    "format_tool_catalog_text",
]
