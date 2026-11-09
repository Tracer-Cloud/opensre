"""Evidence mapper for ec2_instances_by_tag."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry


def map_ec2_instances_by_tag(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the active instance count, primary tier, and tier spread.

    ``truncated`` reflects the tool's own 5000-instance safety cap on
    pagination -- an explicit signal, not an inferred page-size heuristic.
    """
    if not output.get("available"):
        return
    instances = output.get("instances") or []
    if not instances:
        return
    total = output.get("total_instances", len(instances))
    count_label = f"{total}+" if output.get("truncated") else str(total)
    parts = [f"{count_label} active instance(s)"]
    primary_tier = (output.get("summary") or {}).get("primary_tier")
    if primary_tier:
        parts.append(f"primary tier '{primary_tier}'")
    tiers_detected = output.get("tiers_detected") or []
    if len(tiers_detected) > 1:
        parts.append(f"{len(tiers_detected)} tiers")
    record_evidence_entry(
        evidence,
        source="ec2_instances_by_tag",
        label="EC2 Instances",
        summary=", ".join(parts),
    )
