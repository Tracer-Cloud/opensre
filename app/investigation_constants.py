"""Constants shared between pipeline routing and investigation nodes.

This module exists outside both ``app.pipeline`` and ``app.nodes`` to avoid
the circular import that ``app.pipeline.__init__`` → ``graph`` → ``app.nodes``
would otherwise create.
"""

from __future__ import annotations

MAX_INVESTIGATION_LOOPS = 4

# Maximum number of times ``adapt_window`` may replace ``state.incident_window``
# during a single investigation. Each replacement records the previous window
# in ``state.incident_window_history``; once the history reaches this length
# the rule layer no-ops. With ``MAX_INVESTIGATION_LOOPS = 4`` and
# ``MAX_EXPANSIONS = 2`` the worst case is two expansions inside the four-loop
# budget, which is enough to widen 120m → 240m → 480m before deferring to the
# diagnose narrative.
MAX_EXPANSIONS = 2
