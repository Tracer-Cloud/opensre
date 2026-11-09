"""Evidence mapper for execute_python_code."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry, unique_evidence_source
from infrastructure.text.truncation import truncate

#: Bound stdout/stderr echoed into a report summary -- generated code output
#: is unbounded.
_OUTPUT_SUMMARY_TRUNCATE_LEN = 120


def map_execute_python_code(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the execution result: stdout on success, the failure reason otherwise.

    A successful run with no stdout has nothing to cite. A failed run is
    cited even without output, since the failure itself (timeout, non-zero
    exit) is diagnostic information the agent computed.
    """
    stdout = str(output.get("stdout") or "").strip()
    stderr = str(output.get("stderr") or "").strip()
    if output.get("success"):
        if not stdout:
            return
        summary = truncate(stdout.replace("\n", " "), _OUTPUT_SUMMARY_TRUNCATE_LEN)
    else:
        reason = "timed out" if output.get("timed_out") else f"exit code {output.get('exit_code')}"
        summary = f"execution failed ({reason})"
        detail = (stderr or stdout).replace("\n", " ")
        if detail:
            summary += f": {truncate(detail, _OUTPUT_SUMMARY_TRUNCATE_LEN)}"
    record_evidence_entry(
        evidence,
        source=unique_evidence_source(evidence, "execute_python_code"),
        label="Python Execution",
        summary=summary,
    )
