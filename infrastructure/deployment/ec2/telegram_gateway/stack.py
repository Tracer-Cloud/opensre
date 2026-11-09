"""Gateway stack configuration and persisted deployment outputs."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.constants import OPENSRE_HOME_DIR

STACK_NAME = "opensre-gateway"
GATEWAY_PROCESS_NAME = "opensre-gateway"

_STACK_SUFFIX_ENV = "OPENSRE_STACK_SUFFIX"

_OUTPUTS_DIR = OPENSRE_HOME_DIR / "deployments"
_AMI_ID_FILE = _OUTPUTS_DIR / "gateway-id.txt"


@dataclass(frozen=True)
class GatewayStack:
    """Settings for the gateway EC2 deployment."""

    stack_name: str
    gateway_process_name: str


GATEWAY_STACK = GatewayStack(
    stack_name=STACK_NAME,
    gateway_process_name=GATEWAY_PROCESS_NAME,
)


def get_stack() -> GatewayStack:
    """Return the gateway stack config.

    When ``OPENSRE_STACK_SUFFIX`` is set all resource names are suffixed with
    ``-<value>`` so each developer gets isolated AWS resources in a shared
    account (same convention as the main EC2 stack).
    """
    suffix = os.getenv(_STACK_SUFFIX_ENV, "").strip()
    if suffix:
        return GatewayStack(
            stack_name=f"{STACK_NAME}-{suffix}",
            gateway_process_name=f"{GATEWAY_PROCESS_NAME}-{suffix}",
        )
    return GATEWAY_STACK


# ── Outputs ───────────────────────────────────────────────────────────────────


def _outputs_path(*, path: Path | None = None) -> Path:
    if path is not None:
        return path
    stack = get_stack()
    return _OUTPUTS_DIR / f"{stack.stack_name}.json"


def save_outputs(outputs: Mapping[str, Any], *, path: Path | None = None) -> Path:
    """Persist deployment outputs to local user state."""
    stack = get_stack()
    payload = dict(outputs)
    payload.setdefault("StackName", stack.stack_name)

    output_path = _outputs_path(path=path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return output_path


def outputs_exists(*, path: Path | None = None) -> bool:
    return _outputs_path(path=path).exists()


def load_outputs(*, path: Path | None = None) -> dict[str, Any]:
    output_path = _outputs_path(path=path)
    if not output_path.exists():
        stack = get_stack()
        raise FileNotFoundError(
            f"No outputs found for stack '{stack.stack_name}'. Deploy the stack first."
        )
    result = json.loads(output_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Deployment outputs file is malformed.")
    return result


def delete_outputs(*, path: Path | None = None) -> None:
    output_path = _outputs_path(path=path)
    if output_path.exists():
        output_path.unlink()


# ── AMI id ────────────────────────────────────────────────────────────────────


def _ami_id_path(*, path: Path | None = None) -> Path:
    return path if path is not None else _AMI_ID_FILE


def save_ami_id(ami_id: str, *, path: Path | None = None) -> Path:
    """Persist the AMI id produced by ``make build-gateway-image``."""
    ami_path = _ami_id_path(path=path)
    ami_path.parent.mkdir(parents=True, exist_ok=True)
    ami_path.write_text(ami_id.strip() + "\n", encoding="utf-8")
    return ami_path


def load_ami_id(*, path: Path | None = None) -> str:
    """Load the AMI id saved by the last ``make build-gateway-image`` run."""
    ami_path = _ami_id_path(path=path)
    if not ami_path.exists():
        raise FileNotFoundError(
            f"No saved gateway AMI id found at {ami_path}. "
            "Run `make build-gateway-image` first, or set OPENSRE_GATEWAY_AMI_ID."
        )
    return ami_path.read_text(encoding="utf-8").strip()


def ami_id_exists(*, path: Path | None = None) -> bool:
    return _ami_id_path(path=path).exists()
