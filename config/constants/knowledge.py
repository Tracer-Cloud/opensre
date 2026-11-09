"""Environment names and static limits for workspace knowledge.

Workspace knowledge is the organization-wide tier beside each member's
personal memory: what the company is, who is on the team, and what each
connected integration looks like. Background scans write it; every member's
turns read it.
"""

from __future__ import annotations

OPENSRE_KNOWLEDGE_DIR_ENV = "OPENSRE_KNOWLEDGE_DIR"
OPENSRE_KNOWLEDGE_DISABLED_ENV = "OPENSRE_KNOWLEDGE_DISABLED"
# "0" keeps scans to workspace metadata (names, topics, member directory) and
# never reads a channel message.
OPENSRE_KNOWLEDGE_SCAN_MESSAGES_ENV = "OPENSRE_KNOWLEDGE_SCAN_MESSAGES"

KNOWLEDGE_DIRNAME = "knowledge"

#: An integration's knowledge is refreshed once it is older than this.
KNOWLEDGE_SCAN_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
#: How often the gateway checks whether a scan is due.
KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS = 60 * 60
#: How often the list of members who may read workspace knowledge is refreshed,
#: so a new member gets access the same day, not at the next weekly scan.
KNOWLEDGE_READERS_REFRESH_SECONDS = 6 * 60 * 60

__all__ = [
    "KNOWLEDGE_DIRNAME",
    "KNOWLEDGE_READERS_REFRESH_SECONDS",
    "KNOWLEDGE_SCAN_CHECK_INTERVAL_SECONDS",
    "KNOWLEDGE_SCAN_MAX_AGE_SECONDS",
    "OPENSRE_KNOWLEDGE_DIR_ENV",
    "OPENSRE_KNOWLEDGE_DISABLED_ENV",
    "OPENSRE_KNOWLEDGE_SCAN_MESSAGES_ENV",
]
