from __future__ import annotations

from typing import Any


def test_model_switch_resets_runtime_llm_caches(monkeypatch: Any) -> None:
    import surfaces.shared.llm_setup.selection as model_module

    calls: list[str] = []
    monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: calls.append("reset"))

    model_module._reset_runtime_llm_caches()

    assert calls == ["reset"]
