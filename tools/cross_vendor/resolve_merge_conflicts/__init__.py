"""Merge-conflict resolution tool: a coding agent resolves the conflicted files, OpenSRE commits the merge.

Package layout:

- ``tool.py``       — the agent-facing :class:`BaseTool` contract and its instance;
  ``tools/cross_vendor/__init__.py`` lists it for registry discovery.
- ``runner.py``     — the lifecycle: snapshot the stopped merge, run the coding agent
  (via the neutral ``integrations/coding_agent`` seam), verify, show, approve,
  commit, push, watch the checks.
- ``errors.py``     — :class:`ResolveMergeError` + stable ``error_kind`` constants.

The tool never aborts a merge: files the agent cannot settle are reported with
the merge left in progress, so the user decides them. Committing the merge and
pushing the branch go through the shell's ``/auto`` policy (the ``merge_push``
tool type): High proceeds, lower levels ask first, an unattended run proceeds.
"""

from __future__ import annotations

from tools.cross_vendor.resolve_merge_conflicts.tool import (
    ResolveMergeConflictsTool,
    resolve_merge_conflicts,
)

__all__ = ["ResolveMergeConflictsTool", "resolve_merge_conflicts"]
