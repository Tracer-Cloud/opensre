from __future__ import annotations

from pathlib import Path

import pytest

from infrastructure.analytics import install_state


def test_unreadable_marker_is_unknown_without_creating_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def denied(_path: Path) -> None:
        raise PermissionError("unreadable")

    monkeypatch.setattr(Path, "stat", denied)

    assert install_state.snapshot_install_marker(tmp_path) == "unknown"


def test_unknown_snapshot_replaces_previous_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENSRE_INSTALL_MARKER_STATE", "present")
    install_state.record_install_marker_state(tmp_path)
    monkeypatch.setenv("OPENSRE_INSTALL_MARKER_STATE", "unknown")
    install_state.record_install_marker_state(tmp_path)
    monkeypatch.delenv("OPENSRE_INSTALL_MARKER_STATE")

    assert install_state.read_install_marker_state(tmp_path) == "unknown"
    assert not list(tmp_path.glob(".install_marker_state-*"))


def test_failed_snapshot_write_preserves_complete_record_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENSRE_INSTALL_MARKER_STATE", "present")
    install_state.record_install_marker_state(tmp_path)

    def denied(_source: Path, _destination: Path) -> Path:
        raise PermissionError("read-only")

    monkeypatch.setattr(Path, "replace", denied)
    monkeypatch.setenv("OPENSRE_INSTALL_MARKER_STATE", "absent")
    install_state.record_install_marker_state(tmp_path)

    # The current installer still reports its observation if persistence fails.
    assert install_state.read_install_marker_state(tmp_path) == "absent"
    monkeypatch.delenv("OPENSRE_INSTALL_MARKER_STATE")
    assert install_state.read_install_marker_state(tmp_path) == "present"
    assert not list(tmp_path.glob(".install_marker_state-*"))


def test_uninstrumented_install_does_not_infer_a_fresh_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OPENSRE_INSTALL_MARKER_STATE", raising=False)
    install_state.record_install_marker_state(tmp_path)

    assert install_state.read_install_marker_state(tmp_path) is None
    assert not (tmp_path / "installed").exists()
