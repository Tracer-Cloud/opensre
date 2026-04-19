"""Constants shared between pipeline routing and investigation nodes.

This module exists outside both ``app.pipeline`` and ``app.nodes`` to avoid
the circular import that ``app.pipeline.__init__`` → ``graph`` → ``app.nodes``
would otherwise create.
"""

from __future__ import annotations

MAX_INVESTIGATION_LOOPS = 4
