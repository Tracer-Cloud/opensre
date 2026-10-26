"""Shared output validation for non-interactive LLM CLI adapters."""

from __future__ import annotations

from collections.abc import Callable


def require_nonempty_output(
    stdout: str,
    stderr: str,
    returncode: int,
    explain_failure: Callable[..., str],
    *,
    empty_output_error: str | None = None,
) -> str:
    """Return stripped CLI output or raise the adapter's descriptive error."""
    result = (stdout or "").strip()
    if result:
        return result

    message = empty_output_error or (
        f"{explain_failure(stdout=stdout, stderr=stderr, returncode=returncode)} (empty output)"
    )
    raise RuntimeError(message)
