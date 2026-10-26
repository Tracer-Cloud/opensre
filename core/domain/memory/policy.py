"""The one written rule for what goes into long-term memory.

Both writers read this text verbatim: the ``memory_remember`` tool description
(the agent saving mid-turn) and the extraction prompt (the background pass
over a finished session). Change the policy here, never in only one of them.
"""

from __future__ import annotations

MEMORY_WRITE_POLICY = """\
Save durable facts that will help a later session: who the user is and how \
they work, stable preferences, infrastructure conventions, one memory per \
repository (owner/name, purpose, default branch, CI behavior and conventions), \
and lessons from real failures with the fix that worked.
- What the user says is the source of truth for their identity and preferences; \
save it even when they never say "remember".
- Facts verified by tool output in the session (repository facts, CI behavior, \
a failure and its fix, what worked) may be saved with provenance: source "tool" \
and evidence naming the tool and the run, PR, commit or command that shows it. \
Mark a user statement source "user". Something only the assistant said is not \
evidence about infrastructure, repositories or incidents.
- Never save secrets or credentials, demo, sample or synthetic runs and \
repositories, the transient status of work just done, or one-off command output.
- Update the existing memory with the same name instead of adding a near-duplicate."""


__all__ = ["MEMORY_WRITE_POLICY"]
