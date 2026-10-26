"""Reusable interactive-prompt hooks (self-contained UI + key-binding units).

Each hook owns one keyboard-driven prompt element — its rendering and its key
bindings — so hosts wire a clearly-scoped piece rather than scattering the
state, render, and key handling across the prompt loop.
"""

from surfaces.interactive_shell.ui.hooks.confirmation_choice import (
    confirmation_choice_overlay_ansi,
    install_confirmation_key_bindings,
)
from surfaces.interactive_shell.ui.hooks.output_expand import (
    install_output_expand_key_bindings,
)
from surfaces.interactive_shell.ui.hooks.plan_expand import (
    install_plan_expand_key_bindings,
)

__all__ = [
    "confirmation_choice_overlay_ansi",
    "install_confirmation_key_bindings",
    "install_output_expand_key_bindings",
    "install_plan_expand_key_bindings",
]
