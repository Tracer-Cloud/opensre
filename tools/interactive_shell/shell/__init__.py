"""Shell command machinery for the interactive REPL.

Groups the shell command-line concern next to the agent-facing
``tools.interactive_shell.actions.shell``:

* ``parsing`` normalizes passthrough and empty input without parsing shell syntax,
* ``policy`` resolves the (alpha-mode, allow-everything) shell execution plan,
* ``execution`` runs the subprocess and returns a structured result,
* ``runner`` wires normalization, policy, and execution together and records the turn.

Import submodules explicitly (for example ``tools.interactive_shell.shell.runner``)
rather than relying on this package initializer, to keep interactive-shell
startup import-light.
"""

from __future__ import annotations
