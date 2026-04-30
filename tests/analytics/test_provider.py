from __future__ import annotations

from pathlib import Path

from app.analytics import install, provider
from app.analytics.events import Event


class _StubAnalytics:
    def __init__(self) -> None:
        self.events: list[tuple[Event, provider.Properties | None]] = []

    def capture(self, event: Event, properties: provider.Properties | None = None) -> None:
        self.events.append((event, properties))


def test_capture_install_detected_if_needed_captures_once(monkeypatch, tmp_path: Path) -> None:
    stub = _StubAnalytics()
    marker_path = tmp_path / "installed"

    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", marker_path)
    monkeypatch.setattr(provider, "get_analytics", lambda: stub)

    first = provider.capture_install_detected_if_needed({"install_source": "make_install"})
    second = provider.capture_install_detected_if_needed({"install_source": "make_install"})

    assert first is True
    assert second is False
    assert marker_path.exists()
    assert stub.events == [
        (Event.INSTALL_DETECTED, {"install_source": "make_install"}),
    ]


def test_capture_first_run_if_needed_uses_same_install_guard(monkeypatch, tmp_path: Path) -> None:
    stub = _StubAnalytics()

    monkeypatch.setattr(provider, "_FIRST_RUN_PATH", tmp_path / "installed")
    monkeypatch.setattr(provider, "get_analytics", lambda: stub)

    provider.capture_first_run_if_needed()
    provider.capture_first_run_if_needed()

    assert stub.events == [(Event.INSTALL_DETECTED, None)]


def test_get_or_create_anonymous_id_reuses_persisted_value(monkeypatch, tmp_path: Path) -> None:
    anonymous_id_path = tmp_path / "anonymous_id"

    monkeypatch.setattr(provider, "_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(provider, "_ANONYMOUS_ID_PATH", anonymous_id_path)

    first = provider._get_or_create_anonymous_id()
    second = provider._get_or_create_anonymous_id()

    assert first == second
    assert anonymous_id_path.read_text(encoding="utf-8") == first


def test_install_main_reuses_shared_install_guard(monkeypatch) -> None:
    captured: list[provider.Properties | None] = []

    monkeypatch.setattr(
        install,
        "capture_install_detected_if_needed",
        lambda properties=None: captured.append(properties) or True,
    )
    monkeypatch.setattr(install, "shutdown_analytics", lambda **_kwargs: None)

    exit_code = install.main()

    assert exit_code == 0
    assert captured == [{"install_source": "make_install", "entrypoint": "make install"}]
