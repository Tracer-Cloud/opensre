"""What the model and the transcript read back from one Python execution.

The result payload repeats the call's own ``code`` and ``inputs`` (with the
runtime facts merged into them). Sent to the model as JSON, that echo was about
two thirds of a typical result, and the output it had asked for came last, with
its newlines escaped. The model reads the status and the two streams as plain
text instead; the payload stays whole for callers that inspect it, and the
transcript shows one line saying how the run ended.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from core.tool import tool_output_byte_budget, truncate_output_text

#: Share of the output budget stderr may take when stdout needs the rest.
_STDERR_BUDGET_DIVISOR = 4
#: Longest traceback closing line the transcript summary quotes.
_SUMMARY_CAUSE_CHARS = 200
_TIMED_OUT = "Execution timed out"
_MISSING_MODULE = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")
_MISSING_MODULE_HINT = (
    "Hint: {module} is not installed in the sandbox and nothing can be installed "
    "there. Rewrite the code with the standard library."
)
#: The sandbox refuses ctypes, which numpy and pandas load on import.
_NATIVE_CODE_DENIAL = "Importing _ctypes is not permitted in sandbox mode"
_NATIVE_CODE_HINT = (
    "Hint: a package tried to load native code through ctypes, which the sandbox "
    "blocks (numpy and pandas do). Rewrite the code with the standard library, "
    "such as statistics, csv, json and collections."
)


def _hint(stderr: str) -> str | None:
    """What to do about a failure whose fix the traceback does not name."""
    if _NATIVE_CODE_DENIAL in stderr:
        return _NATIVE_CODE_HINT
    missing = _MISSING_MODULE.search(stderr)
    return _MISSING_MODULE_HINT.format(module=missing.group(1)) if missing else None


def execution_observation(payload: Mapping[str, Any]) -> str:
    """The run as plain text: status first, then stdout and stderr, each bounded.

    The status lines sit outside the output budget, so output of any size
    cannot push them out of the result.
    """
    stdout = str(payload.get("stdout") or "")
    stderr = str(payload.get("stderr") or "")
    lines = [_status_line(payload)]
    credentials = payload.get("credentials_available")
    if isinstance(credentials, list) and credentials:
        lines.append(f"Credentials in the environment: {', '.join(map(str, credentials))}")
    hint = _hint(stderr)
    if hint is not None:
        lines.append(hint)

    budget = tool_output_byte_budget()
    shown_stderr = truncate_output_text(
        stderr, budget // _STDERR_BUDGET_DIVISOR if stdout else budget
    )
    shown_stdout = truncate_output_text(stdout, budget - len(shown_stderr.encode("utf-8")))
    lines.extend(("Stdout:", shown_stdout if stdout else "(empty)"))
    if stderr:
        lines.extend(("Stderr:", shown_stderr))
    return "\n".join(lines)


def execution_summary(payload: Mapping[str, Any]) -> str:
    """One line for the transcript: how the run ended, and why when it failed.

    The streams themselves are the model's to report, so the line carries their
    size on success and the closing line of the traceback on failure.
    """
    error = str(payload.get("error") or "").strip()
    if payload.get("timed_out"):
        return error or _TIMED_OUT
    if error:
        return f"Not run: {error}"
    exit_code = payload.get("exit_code")
    if exit_code:
        cause = _last_line(str(payload.get("stderr") or ""))
        return (
            f"Exited with code {exit_code}: {cause}" if cause else f"Exited with code {exit_code}"
        )
    printed = len(str(payload.get("stdout") or "").splitlines())
    if not printed:
        return "Ran with no output"
    return f"Ran and printed {printed} line{'' if printed == 1 else 's'}"


def _last_line(text: str) -> str:
    """The last line that says something, cut to what one transcript row holds."""
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped[:_SUMMARY_CAUSE_CHARS]
    return ""


def _status_line(payload: Mapping[str, Any]) -> str:
    error = str(payload.get("error") or "").strip()
    if payload.get("timed_out"):
        return f"{error or _TIMED_OUT}; output written before then is below."
    if error:
        return f"Not run: {error}"
    return f"Process exited with code {payload.get('exit_code')}"


__all__ = ["execution_observation", "execution_summary"]
