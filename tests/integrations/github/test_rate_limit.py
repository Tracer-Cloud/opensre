"""One GitHub client's rate-limit gate: how long it sends one request at a time."""

from __future__ import annotations

import threading

from integrations.github.rate_limit import RateLimitGate


def _admit_then_signal(gate: RateLimitGate, admitted: threading.Event) -> None:
    gate.admit()
    admitted.set()


def test_one_request_at_a_time_lasts_one_window_past_a_secondary_pause() -> None:
    """One-at-a-time admission had no end, so a large analysis could crawl long past the
    client's patience; it now ends a window after the pause."""
    clock = [100.0]
    gate = RateLimitGate(patience_seconds=5, serial_window_seconds=60, clock=lambda: clock[0])
    assert gate.pause(0.0, secondary=True)

    # Inside the window: a second request waits for the first to finish.
    gate.admit()
    inside = threading.Event()
    threading.Thread(target=_admit_then_signal, args=(gate, inside), daemon=True).start()
    assert not inside.wait(0.2)
    gate.release()
    assert inside.wait(5)
    gate.release()

    # Past the window: two requests are in flight at once again.
    clock[0] += 61
    gate.admit()
    after = threading.Event()
    threading.Thread(target=_admit_then_signal, args=(gate, after), daemon=True).start()
    assert after.wait(5)
    gate.release()
    gate.release()
