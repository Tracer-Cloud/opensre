"""Append-only unread triage notifications that preserve prompt input ownership."""

from __future__ import annotations

import asyncio
import logging
from collections import Counter

from rich.console import Console
from rich.text import Text

from core.agent_harness.spi.session_state import exclusive_stdin_active
from core.domain.alerts.triage.storage import TriageStore
from surfaces.interactive_shell.runtime.core.state import ReplState
from surfaces.interactive_shell.session import Session


async def watch_triage(session: Session, state: ReplState) -> None:
    """Display durable unread summaries after startup and lifecycle updates thereafter."""
    store = TriageStore()
    console = Console(highlight=False)
    first = True
    while not state.exit_requested:
        try:
            if store.path.exists() and not exclusive_stdin_active(session):
                events = await asyncio.to_thread(store.unread)
                if events:
                    if first:
                        counts = Counter(e["kind"] for e in events)
                        console.print(
                            Text(
                                f"Unread triage updates: {dict(counts)}. Read reports with /triage list."
                            )
                        )
                    else:
                        for event in events:
                            identifier = event["occurrence_id"] or event["source_id"]
                            console.print(Text(f"Triage {event['kind']} · {identifier}"))
                    await asyncio.to_thread(store.mark_read, [e["id"] for e in events])
                first = False
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logging.getLogger(__name__).debug(
                "Triage notifications unavailable (%s)", type(exc).__name__
            )
            await asyncio.sleep(1)
