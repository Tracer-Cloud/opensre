"""Backward-compatibility shim — canonical location is app.pipeline.runners."""

from app.pipeline.runners import SimpleAgent, run_chat, run_investigation

__all__ = ["SimpleAgent", "run_chat", "run_investigation"]
