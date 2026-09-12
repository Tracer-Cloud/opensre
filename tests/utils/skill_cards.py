"""Build valid workflow-card fixtures with explicit overrides for contract tests."""

from __future__ import annotations

from datetime import date
from typing import Any

import yaml


def skill_card(name: str, body: str = "Follow the workflow.", **fields: Any) -> str:
    """Render valid frontmatter while permitting deliberately invalid field overrides."""
    frontmatter = {
        "name": name,
        "description": f"Run the {name} workflow.",
        "metadata": {
            "owner": "Author",
            "last_changed_by": "Editor",
            "last_changed_at": date(2026, 1, 1),
            "usecases": ["For maintainers exercising this workflow."],
            "requires": ["The fixture execution environment."],
            "version": "1.0",
        },
        **fields,
    }
    return f"---\n{yaml.safe_dump(frontmatter, sort_keys=False)}---\n{body}"
