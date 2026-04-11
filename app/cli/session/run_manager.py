"""Execution helpers for interactive session investigations."""

from __future__ import annotations

import json
import time
from typing import Any

from rich.console import Console

from app.cli.investigate import run_investigation_cli_streaming
from app.cli.session.state import SessionState

_console = Console(highlight=False)


def run_alert_investigation(state: SessionState, payload: dict[str, Any]) -> None:
    if state.interruption_requested:
        state.interruption_requested = False
    state.active_run = True
    started = time.perf_counter()
    try:
        state.last_alert = payload
        result = run_investigation_cli_streaming(raw_alert=payload)
        state.last_result = result
        state.append_turn(json.dumps(payload, sort_keys=True))
        root_cause = str(result.get("root_cause", "")).strip()
        if root_cause:
            _console.print(f"\nRoot cause: {root_cause}")
    finally:
        state.last_duration_s = time.perf_counter() - started
        state.active_run = False


def run_followup(state: SessionState, text: str) -> None:
    prompt = text.strip()
    if not prompt:
        return
    state.append_turn(prompt)

    if not state.last_alert:
        _console.print("No active alert context. Paste alert JSON to start an investigation.")
        return

    merged_alert = dict(state.last_alert)
    history = merged_alert.get("session_history")
    history_items = list(history) if isinstance(history, list) else []
    history_items.append(prompt)
    merged_alert["session_history"] = history_items
    merged_alert["session_followup"] = prompt

    if state.interruption_requested:
        state.interruption_requested = False
    state.active_run = True
    started = time.perf_counter()
    try:
        result = run_investigation_cli_streaming(raw_alert=merged_alert)
        state.last_alert = merged_alert
        state.last_result = result
        root_cause = str(result.get("root_cause", "")).strip()
        if root_cause:
            _console.print(f"\nUpdated root cause: {root_cause}")
    finally:
        state.last_duration_s = time.perf_counter() - started
        state.active_run = False
