"""Unit tests for helpers shared by relational integrations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from integrations._relational import read_only_query

logger = logging.getLogger(__name__)


@dataclass
class _FakeConfig:
    """Minimal stand-in for a vendor config (only ``is_configured`` is used)."""

    is_configured: bool = True


class _FakeCursor:
    """Cursor doubling as its own context manager, like pymysql/psycopg2 cursors."""

    def __init__(self) -> None:
        self.exited = False

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc_info: Any) -> bool:
        self.exited = True
        return False


class _FakeConnection:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def close(self) -> None:
        self.closed = True


def _runner(connection: _FakeConnection | None = None, *, integration: str = "fakedb"):
    """Build a decorator bound to a fake connection."""
    conn = connection if connection is not None else _FakeConnection(_FakeCursor())
    return read_only_query(integration=integration, logger=logger, connect=lambda _config: conn)


class TestReadOnlyQueryGuard:
    """The wrapper short-circuits before opening a connection."""

    def test_unconfigured_returns_unavailable_envelope(self) -> None:
        @_runner()
        def probe(cursor: Any, config: Any) -> dict[str, Any]:
            raise AssertionError("must not run when unconfigured")

        result = probe(_FakeConfig(is_configured=False))

        assert result == {
            "source": "fakedb",
            "available": False,
            "error": "Not configured.",
            "failure_state": "missing_config",
        }

    def test_unconfigured_never_connects(self) -> None:
        conn = _FakeConnection(_FakeCursor())

        @_runner(conn)
        def probe(cursor: Any, config: Any) -> dict[str, Any]:
            return {"ok": True}

        probe(_FakeConfig(is_configured=False))

        assert conn.closed is False


class TestReadOnlyQueryHappyPath:
    """Configured calls receive a cursor and return the wrapped function's payload."""

    def test_returns_wrapped_result(self) -> None:
        @_runner()
        def probe(cursor: Any, config: Any) -> dict[str, Any]:
            return {"source": "fakedb", "available": True, "rows": 3}

        assert probe(_FakeConfig())["rows"] == 3

    def test_receives_cursor_and_config(self) -> None:
        cursor = _FakeCursor()
        conn = _FakeConnection(cursor)
        config = _FakeConfig()
        seen: dict[str, Any] = {}

        @_runner(conn)
        def probe(cur: Any, cfg: Any) -> dict[str, Any]:
            seen["cursor"] = cur
            seen["config"] = cfg
            return {}

        probe(config)

        assert seen["cursor"] is cursor
        assert seen["config"] is config

    def test_forwards_extra_arguments(self) -> None:
        @_runner()
        def probe(cursor: Any, config: Any, threshold: int = 0) -> dict[str, Any]:
            return {"threshold": threshold}

        assert probe(_FakeConfig(), threshold=42)["threshold"] == 42

    def test_closes_connection(self) -> None:
        conn = _FakeConnection(_FakeCursor())

        @_runner(conn)
        def probe(cursor: Any, config: Any) -> dict[str, Any]:
            return {}

        probe(_FakeConfig())

        assert conn.closed is True

    def test_preserves_function_identity(self) -> None:
        @_runner()
        def probe(cursor: Any, config: Any) -> dict[str, Any]:
            """Docstring stays put."""
            return {}

        assert probe.__name__ == "probe"
        assert probe.__doc__ == "Docstring stays put."


class TestReadOnlyQueryErrors:
    """Failures become the standard unavailable envelope, not exceptions."""

    def test_query_failure_returns_unavailable(self) -> None:
        @_runner()
        def probe(cursor: Any, config: Any) -> dict[str, Any]:
            raise RuntimeError("table gone")

        with patch("integrations._relational.report_validation_failure"):
            result = probe(_FakeConfig())

        assert result == {
            "source": "fakedb",
            "available": False,
            "error": "table gone",
            "failure_state": "missing_config",
        }

    def test_connection_closed_when_query_fails(self) -> None:
        conn = _FakeConnection(_FakeCursor())

        @_runner(conn)
        def probe(cursor: Any, config: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

        with patch("integrations._relational.report_validation_failure"):
            probe(_FakeConfig())

        assert conn.closed is True

    def test_connect_failure_returns_unavailable(self) -> None:
        def _explode(_config: Any) -> Any:
            raise OSError("connection refused")

        decorator = read_only_query(integration="fakedb", logger=logger, connect=_explode)

        @decorator
        def probe(cursor: Any, config: Any) -> dict[str, Any]:
            raise AssertionError("must not run when connect fails")

        with patch("integrations._relational.report_validation_failure"):
            result = probe(_FakeConfig())

        assert result["available"] is False
        assert result["error"] == "connection refused"

    def test_failure_is_reported_with_wrapped_function_name(self) -> None:
        @_runner(integration="fakedb")
        def get_widget_stats(cursor: Any, config: Any) -> dict[str, Any]:
            raise RuntimeError("boom")

        with patch("integrations._relational.report_validation_failure") as reported:
            get_widget_stats(_FakeConfig())

        assert reported.call_count == 1
        kwargs = reported.call_args.kwargs
        assert kwargs["integration"] == "fakedb"
        assert kwargs["method"] == "get_widget_stats"
