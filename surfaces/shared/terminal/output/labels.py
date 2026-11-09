from __future__ import annotations

import re

from rich.text import Text

from infrastructure.terminal.theme import (
    BRAND,
    DIM,
    ERROR,
    GLYPH_ERROR,
    GLYPH_SUCCESS,
    HIGHLIGHT,
    SECONDARY,
    TEXT,
)
from surfaces.shared.terminal.components.time_format import _elapsed_hms, _fmt_timing
from tools.registry import resolve_tool_display_name

# (padded_label, text_color) -- all labels are 6 chars wide. Every badge draws
# from the theme's accent tokens (HIGHLIGHT / BRAND) so the whole phase strip
# stays within the active palette (e.g. all blue shades under the blue theme),
# alternating the two accents for light per-phase distinction.
BADGE_STYLES: dict[str, tuple[str, str]] = {
    "READ": ("READ  ", HIGHLIGHT),
    "PLAN": ("PLAN  ", BRAND),
    "DIAG": ("DIAG  ", BRAND),
    "MERGE": ("MERGE ", HIGHLIGHT),
}

_NODE_EVENT_TYPE: dict[str, str] = {
    "resolve_integrations": "READ",
    "plan_actions": "PLAN",
}

_NODE_PHASE: dict[str, str] = {
    "resolve_integrations": "LOAD",
    "plan_actions": "PLAN",
}

_NODE_LABELS: dict[str, str] = {
    "resolve_integrations": "Loading integrations",
    "plan_actions": "Planning",
}


def _node_event_type(node_name: str) -> str:
    return _NODE_EVENT_TYPE.get(node_name, "DIAG")


def _node_phase_label(node_name: str) -> str:
    return _NODE_PHASE.get(node_name, node_name.upper()[:12])


def _node_label(node_name: str) -> str:
    return _NODE_LABELS.get(node_name, node_name.replace("_", " ").title())


def _humanise_message(message: str) -> str:
    if not message:
        return ""
    m = re.match(r"Planned actions:\s*\[(.+)\]", message)
    if m:
        raw = re.findall(r"'([^']+)'", m.group(1))
        return ", ".join(resolve_tool_display_name(action) for action in raw)
    if "No new actions" in message:
        return ""
    if "integrations" in message.lower() or "resolved" in message.lower():
        m2 = re.search(r"\[(.+)\]", message)
        if m2 and (services := re.findall(r"'([^']+)'", m2.group(1))):
            return ", ".join(services)
    m3 = re.match(r"validity:(\d+%)", message)
    if m3:
        return f"confidence {m3.group(1)}"
    return re.sub(r"^datadog:", "", message)


def build_progress_step_text(
    *,
    node_name: str,
    elapsed_total: float,
    elapsed_step_ms: int | None = None,
    status: str = "active",
    message: str | None = None,
) -> Text:
    ev_type = _node_event_type(node_name)
    badge_label, badge_color = BADGE_STYLES.get(ev_type, BADGE_STYLES["DIAG"])
    label = _node_label(node_name)
    err = status == "error"
    timing = _fmt_timing(elapsed_step_ms) if elapsed_step_ms is not None else ""

    t = Text()
    t.append(f"{_elapsed_hms(elapsed_total)}  ", style=SECONDARY)
    if status == "active":
        t.append("·  ", style=SECONDARY)
    else:
        t.append(f"{GLYPH_ERROR}  " if err else f"{GLYPH_SUCCESS}  ", style=f"bold {ERROR if err else HIGHLIGHT}")
    t.append(badge_label, style=f"bold {badge_color}")
    t.append("  ·  ", style=DIM)
    t.append(label, style=f"bold {TEXT}")
    if msg := _humanise_message(message or ""):
        t.append(f"  {msg}", style=BRAND)
    if timing:
        t.append(f"  {timing}", style=SECONDARY)
    return t
