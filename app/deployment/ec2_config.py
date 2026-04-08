"""Local state for the managed EC2 OpenSRE deployment."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.constants import OPENSRE_HOME_DIR

_REMOTE_OUTPUTS_DIR = OPENSRE_HOME_DIR / "deployments"
_REMOTE_OUTPUTS_FILE = "tracer-ec2-remote.json"


def get_remote_outputs_path(path: Path | None = None) -> Path:
    """Return the persisted managed-EC2 outputs path."""
    return path or (_REMOTE_OUTPUTS_DIR / _REMOTE_OUTPUTS_FILE)


def save_remote_outputs(
    outputs: Mapping[str, Any],
    *,
    path: Path | None = None,
) -> Path:
    """Persist managed EC2 deployment outputs to local user state."""
    output_path = get_remote_outputs_path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(dict(outputs), indent=2, default=str) + "\n", encoding="utf-8")
    return output_path


def load_remote_outputs(*, path: Path | None = None) -> dict[str, Any]:
    """Load managed EC2 deployment outputs from local user state."""
    output_path = get_remote_outputs_path(path)
    if not output_path.exists():
        raise FileNotFoundError(
            "No outputs found for stack 'tracer-ec2-remote'. Deploy the stack first."
        )
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Managed EC2 outputs file is malformed.")
    return result


def delete_remote_outputs(*, path: Path | None = None) -> None:
    """Delete the persisted managed EC2 deployment outputs file."""
    output_path = get_remote_outputs_path(path)
    if output_path.exists():
        output_path.unlink()
