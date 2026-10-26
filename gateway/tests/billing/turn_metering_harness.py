"""Test callback that applies the gateway's bound credit admission."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from gateway.core.billing.turn_metering import admit_metered_turn


def metered_callback(callback: Callable[..., None]) -> Callable[..., None]:
    """Wrap a transport test callback with the production admission hook."""

    def _run(
        text: str,
        session: Any,
        output: Any,
        logger: logging.Logger,
    ) -> None:
        if admit_metered_turn():
            callback(text, session, output, logger)

    return _run


__all__ = ["metered_callback"]
