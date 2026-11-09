"""``PostgresDatabase``: one lazily-opened pool per DSN, every connection bounded."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from gateway.core.storage.postgres import PostgresDatabase


def _install_fake_psycopg2(
    monkeypatch: pytest.MonkeyPatch, parsed_dsn: dict[str, str] | None = None
) -> type:
    """A psycopg2 stand-in that records the kwargs each pool was opened with."""

    class _FakeConnection:
        def cursor(self) -> _FakeConnection:
            return self

        def execute(self, *_args: Any) -> None:
            return None

        def __enter__(self) -> _FakeConnection:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    class _FakePool:
        instances: list[_FakePool] = []

        def __init__(self, _min: int, _max: int, dsn: str, **kwargs: Any) -> None:
            self.dsn = dsn
            self.max = _max
            self.kwargs = kwargs
            self.gets = 0
            self.puts = 0
            _FakePool.instances.append(self)

        def getconn(self) -> _FakeConnection:
            self.gets += 1
            return _FakeConnection()

        def putconn(self, _conn: Any) -> None:
            self.puts += 1

    pool_module = types.ModuleType("psycopg2.pool")
    pool_module.ThreadedConnectionPool = _FakePool  # type: ignore[attr-defined]
    ext_module = types.ModuleType("psycopg2.extensions")
    ext_module.parse_dsn = lambda _dsn: dict(parsed_dsn or {})  # type: ignore[attr-defined]
    root = types.ModuleType("psycopg2")
    root.pool = pool_module  # type: ignore[attr-defined]
    root.extensions = ext_module  # type: ignore[attr-defined]
    for name, module in (
        ("psycopg2", root),
        ("psycopg2.pool", pool_module),
        ("psycopg2.extensions", ext_module),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return _FakePool


def test_every_connection_opens_under_a_bounded_timeout_with_keepalives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """libpq waits forever without connect_timeout; keepalives detect a dropped socket after it."""
    fake_pool = _install_fake_psycopg2(monkeypatch)

    with PostgresDatabase("postgresql://example/db").transaction():
        pass

    kwargs = fake_pool.instances[0].kwargs
    assert 0 < kwargs["connect_timeout"] <= 30
    assert kwargs["keepalives"] == 1
    assert kwargs["keepalives_idle"] > 0


def test_a_dsn_connect_timeout_is_honoured_inside_the_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operators tune through the DSN; the clamp still owns the bound."""
    fake_pool = _install_fake_psycopg2(monkeypatch, {"connect_timeout": "7"})

    with PostgresDatabase("postgresql://example/db?connect_timeout=7").transaction():
        pass

    assert fake_pool.instances[0].kwargs["connect_timeout"] == 7


def test_max_connections_sizes_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool = _install_fake_psycopg2(monkeypatch)

    with PostgresDatabase("postgresql://example/db", max_connections=10).transaction():
        pass

    assert fake_pool.instances[0].max == 10


def _raise_query_error() -> None:
    raise RuntimeError("query exploded")


def test_a_connection_is_returned_to_the_pool_when_the_transaction_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_pool = _install_fake_psycopg2(monkeypatch)
    database = PostgresDatabase("postgresql://example/db")

    with pytest.raises(RuntimeError), database.transaction():
        _raise_query_error()

    pool = fake_pool.instances[0]
    assert pool.gets == 1
    assert pool.puts == 1
