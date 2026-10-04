"""Service provider interface: what a host calls around a turn, by role.

Import the role you need, not this package:

* :mod:`.session_goal` — attach, query, render and drive a multi-turn goal.
* :mod:`.session_state` — session state a host reads and sets around a turn
  (background and trust mode, terminal, auto-command, turn-outcome hints,
  resume notes, transcript compaction).
* :mod:`.cancel` — cooperative cancellation of the running turn.
* :mod:`.accounting` — per-turn and per-run accounting and token totals.
* :mod:`.prompt_chrome` — the want-me-to closer and shell prompt chrome.
* :mod:`.activity` — plain-text tool activity shared by the shell and gateway.
* :mod:`.integrations` — resolve the session's integrations for a turn.
* :mod:`.grounding` — grounding-cache observability and the action-skill catalog.
* :mod:`.skill_releases` — verify, store and activate published skills releases.
* :mod:`.defaults` — the default adapters a host extends or reuses.

What a host *implements* is :mod:`core.agent_harness.ports`; what it *embeds*
is :mod:`core.agent_harness`; what builds and runs the agent is
:mod:`core.agent_harness.runtime`. Import-cheap: nothing here loads the agent loop.
"""

from __future__ import annotations
