"""Evidence mapper for get_elb_target_health."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry


def map_get_elb_target_health(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the healthy/unhealthy target counts and target group count.

    ``available`` is already False on partial coverage (a per-target-group
    API failure) -- the tool sets that itself precisely so a caller never
    mistakes partial data for full coverage, so no extra check is needed
    here beyond the standard availability guard.
    """
    if not output.get("available"):
        return
    healthy = output.get("healthy_targets") or []
    unhealthy = output.get("unhealthy_targets") or []
    if not healthy and not unhealthy:
        return
    stats = output.get("summary") or {}
    tg_count = stats.get("target_group_count", len(output.get("target_groups") or []))
    parts = [
        f"{len(healthy)} healthy, {len(unhealthy)} unhealthy target(s) across {tg_count} target group(s)"
    ]
    unhealthy_states = stats.get("unhealthy_states") or []
    if unhealthy_states:
        parts.append(f"states: {', '.join(unhealthy_states)}")
    record_evidence_entry(
        evidence,
        source="get_elb_target_health",
        label="ELB Target Health",
        summary=", ".join(parts),
    )
