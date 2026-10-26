"""Evidence mapper for get_bitbucket_file_contents."""

from __future__ import annotations

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from infrastructure.text.truncation import truncate

#: Bound the path/ref echoed into a report summary -- caller-supplied and not
#: bounded by the input schema.
_ID_SUMMARY_TRUNCATE_LEN = 80


def _safe_id(value: str) -> str:
    collapsed = value.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return truncate(collapsed, _ID_SUMMARY_TRUNCATE_LEN)


def map_get_bitbucket_file_contents(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the fetched file's path, revision, and content length.

    ``truncated`` reflects the client's own 10000-char cap on file content --
    an explicit signal, not an inferred heuristic.
    """
    if not output.get("available"):
        return
    content = output.get("content")
    if content is None:
        return
    length = len(content)
    count_label = f"{length}+" if output.get("truncated") else str(length)
    parts = [f"{count_label} char(s)"]
    path = output.get("path")
    if path:
        parts.append(f"from '{_safe_id(str(path))}'")
    ref = output.get("ref")
    if ref:
        parts.append(f"at '{_safe_id(str(ref))}'")
    record_evidence_entry(
        evidence,
        source="get_bitbucket_file_contents",
        label="Bitbucket File Contents",
        summary=", ".join(parts),
    )
