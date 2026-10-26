"""Standard process exit codes for opensre.

Follows the convention from clig.dev and POSIX:
  0 - success
  1 - runtime / general error (retrying may help)

``opensre update --check`` adds its own meanings: 0 up to date, 1 update
available, and :data:`UPDATE_CHECK_FAILED` when the latest version could not be
determined, so a script can tell a new release from a network failure.

Lives in ``infrastructure.process`` so any layer (CLI, tools, integrations) can
share the same exit-code contract without importing the CLI package.
"""

from __future__ import annotations

SUCCESS: int = 0
ERROR: int = 1
#: ``update --check``: a newer main build is published.
UPDATE_AVAILABLE: int = 1
#: ``update --check``: the latest version could not be looked up.
UPDATE_CHECK_FAILED: int = 3
