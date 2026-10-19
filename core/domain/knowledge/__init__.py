"""Workspace knowledge: what the agent knows about the organization, shared by every member.

Personal memory (:mod:`core.domain.memory`) holds what one member told the
agent; this tier holds what is true for the whole organization — its profile,
its team, and what each connected integration looks like — written by
background scans and read by every member's turns.
"""

from __future__ import annotations

from core.domain.knowledge.access import (
    caller_may_read_workspace,
    record_workspace_readers,
    workspace_readers,
)
from core.domain.knowledge.render import (
    render_relevant_workspace_entries,
    render_workspace_block,
    render_workspace_view,
)
from core.domain.knowledge.scans import ScanOutcome, Summarize
from core.domain.knowledge.settings import (
    knowledge_available_here,
    knowledge_enabled,
    scan_messages_enabled,
)
from core.domain.knowledge.store import (
    ORGANIZATION_SLUG,
    WORKSPACE_TYPES,
    delete_workspace_entry,
    entry_age_seconds,
    integration_slug,
    knowledge_dir,
    list_workspace_entries,
    load_workspace_entry,
    save_workspace_entry,
    workspace_entry_exists,
    workspace_store,
)

__all__ = [
    "ORGANIZATION_SLUG",
    "WORKSPACE_TYPES",
    "ScanOutcome",
    "Summarize",
    "caller_may_read_workspace",
    "delete_workspace_entry",
    "entry_age_seconds",
    "integration_slug",
    "knowledge_available_here",
    "knowledge_dir",
    "knowledge_enabled",
    "list_workspace_entries",
    "load_workspace_entry",
    "record_workspace_readers",
    "render_relevant_workspace_entries",
    "render_workspace_block",
    "render_workspace_view",
    "save_workspace_entry",
    "scan_messages_enabled",
    "workspace_entry_exists",
    "workspace_readers",
    "workspace_store",
]
