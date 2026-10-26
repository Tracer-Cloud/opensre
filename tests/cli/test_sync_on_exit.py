"""Opt-in remote sync after the interactive shell exits."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from config.constants.filestorage import (
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_EXCLUDE_OFF_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
)
from config.repl_config import ReplConfig
from infrastructure.filestorage import operations as sync_operations
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.enums import SyncRootName
from infrastructure.filestorage.providers.registry import (
    register_object_store,
    unregister_object_store,
)
from infrastructure.filestorage.syncable import SyncRoot
from surfaces.entrypoint import main


class _FakeObjectStore:
    """In-memory ObjectStore that records when the existing engine reaches it."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.objects: dict[str, bytes] = {}

    def list_objects(self, _prefix: str) -> list[RemoteObject]:
        self.events.append("sync")
        return []

    def get_object(self, key: str) -> bytes:
        return self.objects[key]

    def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    def describe(self) -> str:
        return "fake://sync-on-exit"


class _FailingObjectStore(_FakeObjectStore):
    def list_objects(self, _prefix: str) -> list[RemoteObject]:
        self.events.append("sync")
        raise RuntimeError("store unavailable")


@pytest.fixture
def register_store() -> Iterator[Callable[[str, _FakeObjectStore], None]]:
    registered: list[str] = []

    def register(name: str, store: _FakeObjectStore) -> None:
        register_object_store(name, lambda _config: store)
        registered.append(name)

    yield register

    for name in registered:
        unregister_object_store(name)


def _prepare_interactive_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("surfaces.cli.app.startup.run", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.capture_first_run_if_needed", lambda: None)
    monkeypatch.setattr("surfaces.cli.app.capture_cli_invoked", lambda *_args: None)
    monkeypatch.setattr("surfaces.cli.app.shutdown_analytics", lambda **_kwargs: None)
    monkeypatch.setattr("surfaces.cli.app.sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("surfaces.cli.app.sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(
        "config.repl_config.ReplConfig.load",
        classmethod(lambda _cls, **_kwargs: ReplConfig(enabled=True)),
    )


def _configure_remote_sync(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider: str,
    root: SyncRoot,
) -> None:
    monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "test-bucket")
    monkeypatch.setenv(REMOTE_SYNC_EXCLUDE_OFF_ENV, "1")
    monkeypatch.setenv(REMOTE_SYNC_PROVIDER_ENV, provider)
    monkeypatch.setattr(sync_operations, "syncable_roots", lambda: (root,))


def _arrange_sync(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    register_store: Callable[[str, _FakeObjectStore], None],
    *,
    provider: str,
    store_type: type[_FakeObjectStore] = _FakeObjectStore,
) -> tuple[list[str], _FakeObjectStore]:
    events: list[str] = []
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / "turn.jsonl").write_text('{"turn": 1}\n', encoding="utf-8")
    root = SyncRoot(name=SyncRootName.SESSIONS, path=sessions)
    store = store_type(events)
    register_store(provider, store)
    _prepare_interactive_cli(monkeypatch)
    _configure_remote_sync(monkeypatch, provider=provider, root=root)
    return events, store


def test_sync_on_exit_runs_existing_engine_after_shell(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    register_store: Callable[[str, _FakeObjectStore], None],
) -> None:
    # Arrange
    events, store = _arrange_sync(
        monkeypatch,
        tmp_path,
        register_store,
        provider="sync-on-exit-success",
    )

    def _run_shell(**_kwargs: object) -> int:
        events.append("shell")
        assert store.objects == {}
        return 0

    # Act
    with patch("surfaces.interactive_shell.run_repl", side_effect=_run_shell):
        exit_code = main(["--sync-on-exit"])

    # Assert
    assert exit_code == 0
    assert events == ["shell", "sync"]
    assert store.objects == {"sessions/turn.jsonl": b'{"turn": 1}\n'}


def test_repl_exit_stays_manual_without_sync_on_exit_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    register_store: Callable[[str, _FakeObjectStore], None],
) -> None:
    # Arrange
    events, store = _arrange_sync(
        monkeypatch,
        tmp_path,
        register_store,
        provider="sync-on-exit-default",
    )

    def _run_shell(**_kwargs: object) -> int:
        events.append("shell")
        return 0

    # Act
    with patch("surfaces.interactive_shell.run_repl", side_effect=_run_shell):
        exit_code = main([])

    # Assert
    assert exit_code == 0
    assert events == ["shell"]
    assert store.objects == {}


def test_sync_on_exit_failure_preserves_shell_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    register_store: Callable[[str, _FakeObjectStore], None],
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Arrange
    events, _store = _arrange_sync(
        monkeypatch,
        tmp_path,
        register_store,
        provider="sync-on-exit-failure",
        store_type=_FailingObjectStore,
    )
    captured: list[BaseException] = []
    monkeypatch.setattr(
        "surfaces.cli.commands.remote_sync.capture_exception",
        lambda exc, **_kwargs: captured.append(exc),
    )

    def _run_shell(**_kwargs: object) -> int:
        events.append("shell")
        return 7

    # Act
    with patch("surfaces.interactive_shell.run_repl", side_effect=_run_shell):
        exit_code = main(["--sync-on-exit"])

    # Assert
    assert exit_code == 7
    assert events == ["shell", "sync"]
    assert [type(exc) for exc in captured] == [RuntimeError]
    assert "Automatic remote sync failed" in capsys.readouterr().err
