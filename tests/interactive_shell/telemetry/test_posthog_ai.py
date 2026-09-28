from __future__ import annotations

from infrastructure.analytics.events import Event
from infrastructure.analytics.prompt_log.sinks import posthog_ai


def test_capture_ai_generation_uses_analytics_capture(monkeypatch) -> None:
    calls: list[tuple[Event, dict[str, object]]] = []

    class _FakeAnalytics:
        def capture(self, event: Event, properties: dict[str, object] | None = None) -> None:
            calls.append((event, properties or {}))

    monkeypatch.setattr(posthog_ai, "get_analytics", lambda: _FakeAnalytics())
    posthog_ai.capture_ai_generation({"$ai_model": "gpt-test"})
    assert calls == [(Event.AI_GENERATION, {"$ai_model": "gpt-test"})]
