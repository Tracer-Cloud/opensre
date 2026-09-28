from __future__ import annotations

from datetime import UTC, datetime

from surfaces.shared.terminal.components.time_format import (
    format_repl_duration,
    format_repl_timestamp,
)


def test_format_repl_duration() -> None:
    assert format_repl_duration(None) == "—"
    assert format_repl_duration(45) == "45s"
    assert format_repl_duration(60) == "1m 0s"
    assert format_repl_duration(125) == "2m 5s"
    assert format_repl_duration(3600) == "1h 0m"
    assert format_repl_duration(3725) == "1h 2m"


def test_format_repl_timestamp_iso_table_style() -> None:
    dt = datetime(2026, 5, 29, 10, 15, tzinfo=UTC)
    assert format_repl_timestamp(dt.isoformat(), style="table") == dt.astimezone().strftime(
        "%Y-%m-%d %H:%M"
    )


def test_format_repl_timestamp_compact_style() -> None:
    dt = datetime(2026, 5, 29, 10, 15, tzinfo=UTC)
    assert format_repl_timestamp(dt, style="compact") == dt.astimezone().strftime("%m-%d %H:%M")


def test_format_repl_timestamp_unix_utc_style() -> None:
    ts = datetime(2026, 5, 29, 10, 15, tzinfo=UTC).timestamp()
    assert format_repl_timestamp(ts, style="utc") == "2026-05-29 10:15:00 UTC"


def test_format_repl_timestamp_invalid_iso_fallback() -> None:
    assert format_repl_timestamp("not-a-timestamp", style="table") == "not-a-timestamp"
