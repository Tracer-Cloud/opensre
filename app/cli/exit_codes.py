"""Standard exit codes for the opensre CLI.

Follows the convention from clig.dev and POSIX:
  0 - success
  1 - runtime / general error (retrying may help)
  2 - usage error (user invoked the command incorrectly)
"""

from __future__ import annotations

SUCCESS: int = 0
ERROR: int = 1
USAGE_ERROR: int = 2
