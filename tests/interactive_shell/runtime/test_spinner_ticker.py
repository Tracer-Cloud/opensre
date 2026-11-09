from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from surfaces.interactive_shell.runtime.background.workers import BackgroundTaskPool
from surfaces.interactive_shell.runtime.core.state import ReplState, SpinnerState
from surfaces.interactive_shell.session import Session


@pytest.mark.asyncio
async def test_spinner_ticker_invalidates_once_after_streaming_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ticker repaints while streaming and once more on the streaming->idle edge.

    Without the trailing invalidation a stopped investigation would leave the
    last phase label rendered on the prompt until unrelated I/O redraws it.
    """
    state = ReplState()
    spinner = SpinnerState()
    calls: list[int] = []
    pool = BackgroundTaskPool(
        cast(Session, cast(Any, object())),
        state,
        spinner,
        None,
        lambda: calls.append(1),
    )

    ticks = 0
    real_sleep = asyncio.sleep

    async def fake_sleep(_seconds: float) -> None:
        nonlocal ticks
        ticks += 1
        if ticks == 1:
            spinner.set_phase("Investigation")  # streaming on
        elif ticks == 3:
            spinner.stop()  # streaming off (investigation done)
        elif ticks >= 5:
            state.exit_requested = True
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await pool._spinner_ticker()

    # ticks 1-2 stream (2 invalidations) + one trailing edge at tick 3, then
    # silence while idle at tick 4.
    assert len(calls) == 3
