"""Signal handling for the CLI process."""

from __future__ import annotations

import signal


def install_sigint_handler() -> None:
    """Handle Ctrl+C between prompts (when prompt_toolkit is not active).

    prompt_toolkit intercepts Ctrl+C internally while a prompt is running, so the
    key binding in ``prompt_support`` covers that case. This handler covers
    everything else: long-running operations, streaming output, and the gaps
    between prompts.
    """

    def _handler(_signum: int, _frame: object) -> None:
        from infrastructure.terminal.prompt_support import handle_ctrl_c_press

        handle_ctrl_c_press()

    signal.signal(signal.SIGINT, _handler)


__all__ = ["install_sigint_handler"]
