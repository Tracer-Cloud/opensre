"""
Export utilities for investigation results.

This module provides explicit, opt-in persistence of investigation outputs.
By default, main.py returns results in-memory with no filesystem side effects.

Usage:
    from src.presentation.export import export_outputs
    
    final_state = run_investigation(...)
    export_outputs(final_state, Path("./output"))
"""

from pathlib import Path
from typing import TypedDict


class ExportResult(TypedDict):
    """Result of exporting investigation outputs."""
    problem_md_path: Path
    slack_message_path: Path


def export_outputs(final_state: dict, output_dir: Path) -> ExportResult:
    """
    Export investigation outputs to the filesystem.
    
    This is an explicit, opt-in operation. Call this only when you need
    to persist results to disk (e.g., from tests, CLI scripts, or examples).
    
    Args:
        final_state: The final investigation state from run_investigation().
            Must contain 'problem_md' and 'slack_message' keys.
        output_dir: Directory to write output files. Will be created if needed.
        
    Returns:
        ExportResult with paths to the written files.
        
    Raises:
        KeyError: If final_state is missing required keys.
        OSError: If files cannot be written.
    """
    # Validate required keys
    if "problem_md" not in final_state:
        raise KeyError("final_state missing required key: 'problem_md'")
    if "slack_message" not in final_state:
        raise KeyError("final_state missing required key: 'slack_message'")
    
    # Create output directory
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Write problem.md
    md_path = output_dir / "problem.md"
    md_path.write_text(final_state["problem_md"])
    
    # Write slack_message.txt
    slack_path = output_dir / "slack_message.txt"
    slack_path.write_text(final_state["slack_message"])
    
    return ExportResult(
        problem_md_path=md_path,
        slack_message_path=slack_path,
    )

