"""Remove terminal plan snapshots from user-facing text."""

import re

# ``Plan · n/m`` and the settled headers (``Plan complete``, ``Plan ended``,
# ``Plan ready``), plus the checklist and work-note rows that belong to them.
# A ``completed`` / ``executed`` suffix is part of the header only when the
# line ends there or continues into the blocked count — not mid-sentence.
_PLAN_SNAPSHOT_RE = re.compile(
    r"Plan(?:[ \t]+(?:complete|ended|ready))?[ \t]*[·.][ \t]*\d+[ \t]*/[ \t]*\d+"
    r"(?:"
    r"[ \t]+completed(?=[ \t]*(?:\n|$|[✓●○⊘]|[·.][ \t]*\d+[ \t]+blocked))"
    r"(?:[ \t]*[·.][ \t]*\d+[ \t]+blocked)?"
    r"|[ \t]*[·.][ \t]*\d+[ \t]+blocked"
    r"|[ \t]+executed(?=[ \t]*(?:\n|$))"
    r")?"
    r"(?:[ \t]+[✓●○⊘][^✓●○⊘\n]*)*"
    r"(?:[ \t]*\n[ \t]*(?:[✓●○⊘]|↳)[^\n]*)*"
)


def strip_plan_snapshots(text: str) -> str:
    """Remove checklist snapshots the model restates in its reply.

    The plan lives in the pinned overlay on a terminal, and chat replies must
    not carry it. Echoing ``Plan · n/m``, ``Plan complete``, ``Plan ended``,
    or ``Plan ready`` — and the ✓ / ● / ○ / ⊘ / ↳ rows under them — is a
    redundant wall. Prose (``-``/``•`` bullets, sentences) is untouched."""
    if "Plan" not in text:
        return text
    cleaned = _PLAN_SNAPSHOT_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()
