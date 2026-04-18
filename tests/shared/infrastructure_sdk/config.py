"""Stack output management - save/load outputs."""

import json
from pathlib import Path
from typing import Any

# Output files are stored in this directory
OUTPUTS_DIR = Path(__file__).parent / "outputs"


def _ensure_outputs_dir() -> None:
    """Ensure the outputs directory exists."""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def _get_output_path(stack_name: str) -> Path:
    """Get the path to an output file."""
    return OUTPUTS_DIR / f"{stack_name}.json"


def save_outputs(stack_name: str, outputs: dict[str, Any]) -> None:
    """Save stack outputs to local JSON file for tests to read.

    Args:
        stack_name: Name of the stack.
        outputs: Dictionary of outputs to save.
    """
    _ensure_outputs_dir()
    output_path = _get_output_path(stack_name)
    with open(output_path, "w") as f:
        json.dump(outputs, f, indent=2, default=str)


def load_outputs(stack_name: str) -> dict[str, Any]:
    """Load stack outputs from JSON file.

    Args:
        stack_name: Name of the stack.

    Returns:
        Dictionary of outputs.

    Raises:
        FileNotFoundError: If outputs file doesn't exist.
    """
    output_path = _get_output_path(stack_name)
    if not output_path.exists():
        raise FileNotFoundError(f"No outputs found for stack '{stack_name}'. Deploy the stack first.")
    with open(output_path) as f:
        result: dict[str, Any] = json.load(f)
        return result


def get_output(stack_name: str, key: str) -> str:
    """Get single output value.

    Args:
        stack_name: Name of the stack.
        key: Output key to retrieve.

    Returns:
        The output value as a string.

    Raises:
        KeyError: If the key doesn't exist.
    """
    outputs = load_outputs(stack_name)
    if key not in outputs:
        raise KeyError(f"Output '{key}' not found in stack '{stack_name}'. Available: {list(outputs.keys())}")
    return str(outputs[key])


def delete_outputs(stack_name: str) -> None:
    """Delete stack outputs file.

    Args:
        stack_name: Name of the stack.
    """
    output_path = _get_output_path(stack_name)
    if output_path.exists():
        output_path.unlink()


def list_stacks() -> list[str]:
    """List all stacks that have saved outputs.

    Returns:
        List of stack names.
    """
    _ensure_outputs_dir()
    return [p.stem for p in OUTPUTS_DIR.glob("*.json")]
