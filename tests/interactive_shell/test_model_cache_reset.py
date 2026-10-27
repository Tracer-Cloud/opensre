from __future__ import annotations

from typing import Any


def test_model_switch_resets_runtime_llm_caches(monkeypatch: Any, tmp_path: Any) -> None:
    from surfaces.shared.llm_setup.selection import select_provider

    calls: list[str] = []
    monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: calls.append("reset"))
    monkeypatch.setattr("surfaces.shared.llm_setup.env_sync.PROJECT_ENV_PATH", tmp_path / ".env")
    monkeypatch.setattr("config.setup_store.get_store_path", lambda: tmp_path / "setup.json")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic")
    select_provider("openai", "gpt-5.4-mini")
    assert calls == ["reset"]
