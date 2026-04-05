"""Programmatic SDK entry point — public API for running investigations."""

from __future__ import annotations

from typing import Any


def run_investigation(*args: Any, **kwargs: Any) -> Any:
    """Lazily import the full runner stack to avoid optional dependency churn at import time."""
    from app.pipeline.runners import run_investigation as _run_investigation

    return _run_investigation(*args, **kwargs)
