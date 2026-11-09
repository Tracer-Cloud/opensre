"""Setup writes must serialize and preserve earlier configuration on failure."""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from queue import Queue
from typing import Any

import pytest
from filelock import FileLock, Timeout

from config import setup_store


def test_model_and_remote_saves_preserve_both_successful_updates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "opensre.json"
    setup_store.update_local_llm_selection(provider="old", model="old", path=path)
    original_load = setup_store._load_raw
    original_acquire = FileLock.acquire
    events: Queue[str] = Queue()
    remote_writes: list[Future[None]] = []
    started_remote = False

    def observe_acquire(lock: Any, *args: Any, **kwargs: Any) -> Any:
        if threading.current_thread().name.startswith("setup-remote-writer"):
            events.put("attempting lock")
        return original_acquire(lock, *args, **kwargs)

    def save_remote() -> None:
        setup_store.save_named_remote(
            "production", "http://127.0.0.1:9999", set_active=True, path=path
        )
        events.put("remote saved")

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="setup-remote-writer") as pool:

        def interleaved_load(target: Path | None = None, **kwargs: Any) -> dict[str, Any]:
            nonlocal started_remote
            snapshot = original_load(target, **kwargs)
            if not started_remote:
                started_remote = True
                remote_writes.append(pool.submit(save_remote))
                # An unlocked writer finishes first and its update is then lost.
                # A locked writer signals before waiting, letting this writer finish.
                events.get(timeout=5)
            return snapshot

        monkeypatch.setattr(FileLock, "acquire", observe_acquire)
        monkeypatch.setattr(setup_store, "_load_raw", interleaved_load)
        setup_store.update_local_llm_selection(provider="new", model="new", path=path)
        for write in remote_writes:
            write.result(timeout=5)

    saved = setup_store.load_local_config(path)
    assert saved["targets"]["local"]["model"] == "new"
    assert setup_store.load_named_remotes(path) == {"production": "http://127.0.0.1:9999"}
    assert setup_store.load_remote_url(path) == "http://127.0.0.1:9999"


@pytest.mark.parametrize("failure_point", ["os.fsync", "os.replace"])
def test_failed_publish_keeps_previous_config_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    path = tmp_path / "opensre.json"
    setup_store.save_named_remote("production", "http://127.0.0.1:9999", path=path)
    before = path.read_bytes()

    def failed_publish(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated atomic publication failure")

    monkeypatch.setattr(failure_point, failed_publish)
    with pytest.raises(OSError, match="publication failure"):
        setup_store.update_local_llm_selection(provider="new", model="new", path=path)
    assert path.read_bytes() == before
    assert {file.name for file in tmp_path.iterdir()} <= {"opensre.json", "opensre.json.lock"}


@pytest.mark.parametrize("operation", ["model", "remote", "delete", "scope", "wizard"])
@pytest.mark.parametrize("payload", [b'{"remote":', b"\xff", b"[]"])
def test_mutations_preserve_an_unreadable_setup_store(
    tmp_path: Path, operation: str, payload: bytes
) -> None:
    path = tmp_path / "opensre.json"
    path.write_bytes(payload)
    with pytest.raises(OSError, match="read safely"):
        if operation == "model":
            setup_store.update_local_llm_selection(provider="new", model="new", path=path)
        elif operation == "remote":
            setup_store.save_named_remote("new", "http://127.0.0.1:9998", path=path)
        elif operation == "delete":
            setup_store.delete_named_remote("production", path)
        elif operation == "scope":
            setup_store.save_remote_ops_config(
                provider="railway", project="p", service="s", path=path
            )
        else:
            setup_store.save_local_config(
                wizard_mode="quickstart",
                provider="new",
                model="new",
                api_key_env="",
                model_env="",
                probes={},
                path=path,
            )
    assert path.read_bytes() == payload
    assert setup_store.load_local_config(path)["targets"] == {}


def test_lock_timeout_preserves_config_and_releases_after_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "opensre.json"
    setup_store.save_named_remote("production", "http://127.0.0.1:9999", path=path)
    before = path.read_bytes()
    monkeypatch.setattr(setup_store, "_LOCK_TIMEOUT_SECONDS", 0.01)

    with FileLock(str(path) + ".lock"), pytest.raises(Timeout):
        setup_store.update_local_llm_selection(provider="new", model="new", path=path)
    assert path.read_bytes() == before

    setup_store.update_local_llm_selection(provider="new", model="new", path=path)
    assert setup_store.load_local_config(path)["targets"]["local"]["model"] == "new"
    assert setup_store.load_named_remotes(path) == {"production": "http://127.0.0.1:9999"}


def test_a_successful_publish_remains_valid_json_with_existing_sections(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "opensre.json"
    setup_store.save_named_remote("production", "http://127.0.0.1:9999", path=path)
    setup_store.update_local_llm_selection(provider="new", model="new", path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["remote"]["remotes"]["production"]["url"] == "http://127.0.0.1:9999"
    assert saved["targets"]["local"]["model"] == "new"
