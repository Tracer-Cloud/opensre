"""Backward-compatibility shim — canonical location is app.pipeline.routing."""

from app.pipeline.routing import (
    route_after_extract,
    route_by_mode,
    route_chat,
    route_investigation_loop,
    should_call_tools,
    should_continue_investigation,
)

__all__ = [
    "route_after_extract",
    "route_by_mode",
    "route_chat",
    "route_investigation_loop",
    "should_call_tools",
    "should_continue_investigation",
]
