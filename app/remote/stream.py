"""SSE stream parser for LangGraph API streaming responses."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class StreamEvent:
    """A parsed event from a LangGraph SSE stream.

    Attributes:
        event_type: The SSE event type (e.g. "updates", "metadata", "end").
        node_name: The graph node that produced this event, if applicable.
        data: The parsed JSON payload.
        timestamp: Monotonic timestamp when this event was received.
    """

    event_type: str
    data: dict[str, Any] = field(default_factory=dict)
    node_name: str = ""
    timestamp: float = field(default_factory=time.monotonic)


def parse_sse_stream(response: httpx.Response) -> Iterator[StreamEvent]:
    """Parse an SSE byte stream from a LangGraph `/runs/stream` response.

    LangGraph SSE format:
        event: <type>
        data: <json>
        \\n

    Yields StreamEvent for each complete SSE event.
    """
    current_event_type = ""
    data_lines: list[str] = []

    for line in response.iter_lines():
        if line.startswith("event:"):
            current_event_type = line[len("event:"):].strip()
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "":
            if current_event_type and data_lines:
                raw = "\n".join(data_lines)
                yield _build_event(current_event_type, raw)
                current_event_type = ""
                data_lines = []

    if current_event_type and data_lines:
        raw = "\n".join(data_lines)
        yield _build_event(current_event_type, raw)


def _build_event(event_type: str, raw_data: str) -> StreamEvent:
    """Build a StreamEvent from raw SSE fields."""
    try:
        data = json.loads(raw_data) if raw_data else {}
    except json.JSONDecodeError:
        data = {"raw": raw_data}

    node_name = _extract_node_name(event_type, data)
    return StreamEvent(event_type=event_type, data=data, node_name=node_name)


def _extract_node_name(event_type: str, data: dict[str, Any]) -> str:
    """Extract the graph node name from an event payload.

    LangGraph "updates" events have the node name as the top-level key.
    LangGraph "events" have it in metadata or as the "name" field.
    """
    if event_type == "updates" and isinstance(data, dict):
        keys = [k for k in data if not k.startswith("__")]
        if len(keys) == 1:
            return keys[0]

    if isinstance(data, dict):
        if "name" in data:
            return str(data["name"])
        metadata = data.get("metadata", {})
        if isinstance(metadata, dict) and "langgraph_node" in metadata:
            return str(metadata["langgraph_node"])

    return ""
