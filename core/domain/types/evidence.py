"""Evidence source type — a vendor/integration key identifying a data source.

Not a closed enum: each ``integrations/<vendor>/`` package owns its own
source string(s) (the value passed as ``source=`` when registering a tool).
Core only declares the type alias; it must not hardcode the set of vendors.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

EvidenceSource = str

#: Lifts a tool's raw dict output into the canonical report keys the evidence
#: catalog cites. Called as ``mapper(evidence, output, tool_input)`` and mutates
#: ``evidence`` in place. Declared per tool (``@tool(evidence_mapper=...)``) so a
#: vendor's mapping lives with the vendor's tool, never in a shared stage file.
EvidenceMapper = Callable[[dict[str, Any], dict[str, Any], dict[str, Any]], None]

#: Key under which mappers accumulate citeable report entries in the evidence dict.
CATALOG_ENTRIES_KEY = "catalog_entries"


def record_evidence_entry(
    evidence: dict[str, Any],
    *,
    source: str,
    label: str,
    summary: str | None = None,
    url: str | None = None,
    snippet: str | None = None,
) -> None:
    """Record a citeable report entry from inside an evidence mapper.

    The report's evidence catalog turns each entry into a display id (``E1`` …)
    the agent can cite. ``source`` is the claim-facing key; the first entry for a
    given ``source`` wins, and a bespoke catalog reader for the same key takes
    precedence. Lets a tool's output become citeable without editing the shared
    catalog builder.
    """
    entries = evidence.setdefault(CATALOG_ENTRIES_KEY, [])
    if not isinstance(entries, list):
        return
    entries.append(
        {"source": source, "label": label, "summary": summary, "url": url, "snippet": snippet}
    )


def unique_evidence_source(evidence: dict[str, Any], base: str) -> str:
    """Disambiguate repeat calls to a tool that can run many times per run.

    ``record_evidence_entry`` lets the first entry for a given ``source``
    win, which silently drops every later call's evidence for a tool the
    agent invokes repeatedly with different arguments (e.g. a generic
    dispatcher or a recall/search tool called once per topic). Call this to
    get ``base`` on the first use and an incrementing ``base#N`` suffix on
    each repeat, so every call keeps its own citeable entry.
    """
    entries = evidence.get(CATALOG_ENTRIES_KEY)
    if not isinstance(entries, list):
        return base
    existing = {e.get("source") for e in entries if isinstance(e, dict)}
    if base not in existing:
        return base
    suffix = 2
    while f"{base}#{suffix}" in existing:
        suffix += 1
    return f"{base}#{suffix}"
