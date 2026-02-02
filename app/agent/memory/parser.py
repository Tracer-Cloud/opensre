"""Memory content parsing and extraction."""

from typing import Any


def extract_keywords_from_md(md_content: str, max_chars: int = 2000) -> str:
    """
    Extract key patterns from markdown files.

    Simple heuristic extraction:
    - Headings (## lines)
    - Bullet lists (- lines)
    - Lines containing key investigation terms

    Args:
        md_content: Markdown content to parse
        max_chars: Maximum characters to extract

    Returns:
        Extracted content string
    """
    lines = md_content.split("\n")
    extracted = []

    # Keywords that indicate useful investigation patterns
    keywords = [
        "schema",
        "audit",
        "external api",
        "lambda",
        "s3",
        "root cause",
        "failure",
        "missing field",
        "validation",
        "prefect",
        "airflow",
        "ecs",
    ]

    for line in lines:
        line_stripped = line.strip()
        if not line_stripped:
            continue

        # Include headings, bullets, or keyword lines
        if (
            line_stripped.startswith("#")
            or line_stripped.startswith("-")
            or line_stripped.startswith("*")
            or any(kw in line_stripped.lower() for kw in keywords)
        ):
            extracted.append(line_stripped)

    # Join and truncate
    result = "\n".join(extracted)
    if len(result) > max_chars:
        result = result[:max_chars] + "\n... (truncated)"

    return result


def parse_memory_sections(content: str) -> dict:
    """
    Parse structured sections from memory file content.

    Extracts:
    - action_sequence from "## Investigation Path"
    - root_cause_pattern from "## Root Cause"
    - problem_pattern from "## Problem Pattern"
    - data_lineage from "## Data Lineage"
    - asset_inventory from "## Asset Inventory"
    - service_map from "## Service Map"

    Args:
        content: Memory file content

    Returns:
        Dict with extracted sections
    """
    sections: dict[str, Any] = {}

    # Extract action sequence
    if "## Investigation Path" in content:
        path_section = content.split("## Investigation Path")[1].split("##")[0]
        actions = [
            line.split(". ", 1)[1] for line in path_section.strip().split("\n") if ". " in line
        ]
        sections["action_sequence"] = actions

    # Extract root cause pattern
    if "## Root Cause" in content:
        root_cause = content.split("## Root Cause")[1].split("##")[0].strip()
        sections["root_cause_pattern"] = root_cause

    # Extract problem pattern
    if "## Problem Pattern" in content:
        problem = content.split("## Problem Pattern")[1].split("##")[0].strip()
        sections["problem_pattern"] = problem

    # Extract data lineage
    if "## Data Lineage" in content:
        lineage = content.split("## Data Lineage")[1].split("##")[0].strip()
        sections["data_lineage"] = lineage

    # Extract asset inventory
    if "## Asset Inventory" in content:
        inventory = content.split("## Asset Inventory")[1].split("##")[0].strip()
        sections["asset_inventory"] = inventory

    # Extract service map (parse JSON from code block)
    if "## Service Map" in content:
        service_map_section = content.split("## Service Map")[1].split("##")[0]
        # Extract JSON from code block
        if "```json" in service_map_section:
            json_content = service_map_section.split("```json")[1].split("```")[0].strip()
            try:
                import json

                sections["service_map"] = json.loads(json_content)
            except json.JSONDecodeError:
                sections["service_map"] = None
        else:
            sections["service_map"] = None

    return sections
