"""Code both terminal surfaces build on.

``surfaces/cli`` and ``surfaces/interactive_shell`` are peers and never import
each other; anything they share lives here — the terminal tier (``terminal/``),
LLM provider setup (``llm_setup/``), error rendering (``error_handling/``),
doctor checks and the bundled demo alert — or in a lower layer. Prefer ``core``,
``platform`` or ``config`` when the code has no terminal or surface concern.
"""

from __future__ import annotations
