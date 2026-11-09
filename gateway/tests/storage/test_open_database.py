"""``open_database`` + the domain selectors: one migrated database per process, in-memory without a DSN."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from config.constants.gateway import DATABASE_URL_ENV
from gateway.core.storage import open_database
from gateway.core.storage.events.repository import (
    InMemoryHandledSlackEventRepository,
    PostgresHandledSlackEventRepository,
    handled_slack_event_repository,
)


def _install_fake_psycopg2(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """A psycopg2 stand-in that records every pool it opens."""
    pools: list[Any] = []

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
        def __init__(self, _min: int, _max: int, dsn: str, **_kwargs: Any) -> None:
            self.dsn = dsn
            pools.append(self)

        def getconn(self) -> _FakeConnection:
            return _FakeConnection()

        def putconn(self, _conn: Any) -> None:
            return None

    pool_module = types.ModuleType("psycopg2.pool")
    pool_module.ThreadedConnectionPool = _FakePool  # type: ignore[attr-defined]
    ext_module = types.ModuleType("psycopg2.extensions")
    ext_module.parse_dsn = lambda _dsn: {}  # type: ignore[attr-defined]
    root = types.ModuleType("psycopg2")
    root.pool = pool_module  # type: ignore[attr-defined]
    root.extensions = ext_module  # type: ignore[attr-defined]
    for name, module in (
        ("psycopg2", root),
        ("psycopg2.pool", pool_module),
        ("psycopg2.extensions", ext_module),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    return pools


def test_no_dsn_gives_process_local_repositories(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATABASE_URL_ENV, raising=False)

    database = open_database()

    assert database is None
    assert isinstance(handled_slack_event_repository(database), InMemoryHandledSlackEventRepository)


def test_a_dsn_gives_postgres_repositories_over_one_shared_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every repository sits on the same database, so the process opens one pool."""
    pools = _install_fake_psycopg2(monkeypatch)
    monkeypatch.setenv(DATABASE_URL_ENV, "postgresql://example/db")

    database = open_database()

    assert database is not None
    assert isinstance(handled_slack_event_repository(database), PostgresHandledSlackEventRepository)
    assert len(pools) == 1  # migrations ran for every table through one pool
    assert pools[0].dsn == "postgresql://example/db"
