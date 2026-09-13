"""Langfuse adapter: opt-in gating, secret masking at export, and port mapping.

Tracing is optional for every contributor, so the failure modes worth pinning
are the ones that would either turn it on by accident, leak a credential once
it is on, or let a backend hiccup break a turn.
"""

from __future__ import annotations

import logging
import sys
from contextlib import contextmanager
from typing import Any

import pytest
from langfuse.types import MaskOtelSpansParams, OtelSpanData, OtelSpanIdentifier

from config.constants import (
    LANGFUSE_BASE_URL_ENV,
    LANGFUSE_HOST_ENV,
    LANGFUSE_PUBLIC_KEY_ENV,
    LANGFUSE_SECRET_KEY_ENV,
    OPENSRE_LANGFUSE_DISABLED_ENV,
)
from infrastructure.observability.langfuse import (
    init_langfuse_tracing,
    resolve_langfuse_settings,
)
from infrastructure.observability.langfuse.masking import mask_otel_spans
from infrastructure.observability.langfuse.sink import LangfuseObservationSink
from infrastructure.observability.trace.observations import (
    NOOP_OBSERVATION,
    GenerationUsage,
    ObservationKind,
    ObservationLevel,
    TraceAttributes,
    is_observation_sink_active,
)

_GITHUB_PAT = "ghp_" + "A" * 36
_KEYS = {LANGFUSE_PUBLIC_KEY_ENV: "pk-lf-test", LANGFUSE_SECRET_KEY_ENV: "sk-lf-test"}


# --------------------------------------------------------------------------- settings


def test_settings_require_both_keys_and_honour_disable_switch() -> None:
    assert resolve_langfuse_settings({}) is None
    assert resolve_langfuse_settings({LANGFUSE_PUBLIC_KEY_ENV: "pk-lf-test"}) is None
    assert resolve_langfuse_settings({**_KEYS, OPENSRE_LANGFUSE_DISABLED_ENV: "true"}) is None

    settings = resolve_langfuse_settings(_KEYS)
    assert settings is not None
    assert settings.base_url == "https://cloud.langfuse.com"


def test_settings_prefer_base_url_over_legacy_host() -> None:
    both = {**_KEYS, LANGFUSE_HOST_ENV: "https://legacy/", LANGFUSE_BASE_URL_ENV: "https://eu/"}
    legacy_only = {**_KEYS, LANGFUSE_HOST_ENV: "https://legacy/"}
    assert resolve_langfuse_settings(both).base_url == "https://eu"  # type: ignore[union-attr]
    assert resolve_langfuse_settings(legacy_only).base_url == "https://legacy"  # type: ignore[union-attr]


# --------------------------------------------------------------------------- masking


def _span(span_id: str, attributes: dict[str, Any]) -> tuple[OtelSpanIdentifier, OtelSpanData]:
    identifier = OtelSpanIdentifier(trace_id="t", span_id=span_id)
    return identifier, OtelSpanData(
        trace_id="t",
        span_id=span_id,
        parent_span_id=None,
        name="tool",
        instrumentation_scope_name="langfuse-sdk",
        instrumentation_scope_version=None,
        attributes=attributes,
        resource_attributes={},
    )


def test_mask_otel_spans_redacts_credentials_and_leaves_clean_spans_alone() -> None:
    leaky_id, leaky = _span(
        "leaky",
        {
            "langfuse.observation.output": f'{{"token": "{_GITHUB_PAT}"}}',
            "langfuse.observation.metadata.tags": ["ok", f"Authorization: Bearer {_GITHUB_PAT}"],
            "count": 3,
        },
    )
    clean_id, clean = _span("clean", {"langfuse.observation.input": "hello"})

    result = mask_otel_spans(params=MaskOtelSpansParams(spans={leaky_id: leaky, clean_id: clean}))

    assert set(result.span_patches) == {leaky_id}
    patch = result.span_patches[leaky_id]
    assert patch is not None
    assert _GITHUB_PAT not in str(patch.set_attributes)
    assert "[REDACTED:github_pat]" in str(patch.set_attributes["langfuse.observation.output"])
    assert patch.set_attributes["langfuse.observation.metadata.tags"][0] == "ok"  # type: ignore[index]
    assert "count" not in patch.set_attributes


# --------------------------------------------------------------------------- sink


class _FakeSpan:
    def __init__(self, kwargs: dict[str, Any]) -> None:
        self.start_kwargs = kwargs
        self.updates: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class _FakeLangfuse:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.spans: list[_FakeSpan] = []
        self.fail_start = fail_start

    @contextmanager
    def start_as_current_observation(self, **kwargs: Any) -> Any:
        if self.fail_start:
            raise RuntimeError("exporter down")
        span = _FakeSpan(kwargs)
        self.spans.append(span)
        yield span


def _sink(client: _FakeLangfuse) -> LangfuseObservationSink:
    return LangfuseObservationSink(client)  # type: ignore[arg-type]


def test_generation_carries_model_and_exclusive_usage_buckets() -> None:
    client = _FakeLangfuse()
    with _sink(client).observe(
        ObservationKind.GENERATION, "think", model="gpt-x", input=[{"role": "user"}]
    ) as observation:
        observation.update(
            output={"role": "assistant", "content": "hi"},
            usage=GenerationUsage(
                input_tokens=120, output_tokens=5, cache_read_tokens=100, cache_creation_tokens=None
            ),
        )

    (span,) = client.spans
    assert span.start_kwargs["as_type"] == "generation"
    assert span.start_kwargs["model"] == "gpt-x"
    (update,) = span.updates
    assert update["usage_details"] == {"input": 120, "output": 5}
    assert update["metadata"] == {"cache_read_tokens": 100}


def test_non_generation_kinds_never_pass_model() -> None:
    client = _FakeLangfuse()
    with _sink(client).observe(ObservationKind.TOOL, "echo", model="ignored"):
        pass
    assert client.spans[0].start_kwargs["as_type"] == "tool"
    assert "model" not in client.spans[0].start_kwargs


def test_credential_keys_are_redacted_before_reaching_the_sdk() -> None:
    """Key-based redaction runs in-process; value patterns are caught at export."""
    client = _FakeLangfuse()
    with _sink(client).observe(
        ObservationKind.TOOL, "echo", input={"token": _GITHUB_PAT, "repo": "o/r"}
    ) as observation:
        observation.update(output={"password": "hunter2", "_client": object()})
    span = client.spans[0]
    assert span.start_kwargs["input"] == {"token": "[redacted]", "repo": "o/r"}
    assert span.updates[0]["output"] == {"password": "[redacted]", "_client": "[runtime object]"}


def test_body_exception_marks_error_and_propagates() -> None:
    client = _FakeLangfuse()
    with (
        pytest.raises(ValueError, match="boom"),
        _sink(client).observe(ObservationKind.SPAN, "handle-turn"),
    ):
        raise ValueError(f"boom {_GITHUB_PAT}")
    (update,) = client.spans[0].updates
    assert update["level"] == ObservationLevel.ERROR.value
    assert update["status_message"].startswith("ValueError: boom")
    assert _GITHUB_PAT not in update["status_message"]


def test_backend_failure_degrades_to_noop_observation() -> None:
    client = _FakeLangfuse(fail_start=True)
    ran = False
    with _sink(client).observe(
        ObservationKind.SPAN, "handle-turn", trace=TraceAttributes(session_id="s1")
    ) as observation:
        ran = True
        observation.update(output="still fine")
    assert ran is True
    assert observation is NOOP_OBSERVATION


# --------------------------------------------------------------------------- install


@pytest.fixture
def _enabled_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_LANGFUSE_DISABLED_ENV, raising=False)
    for key, value in _KEYS.items():
        monkeypatch.setenv(key, value)


def test_init_without_keys_leaves_noop_sink(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_LANGFUSE_DISABLED_ENV, raising=False)
    monkeypatch.delenv(LANGFUSE_PUBLIC_KEY_ENV, raising=False)
    monkeypatch.delenv(LANGFUSE_SECRET_KEY_ENV, raising=False)

    assert init_langfuse_tracing() is False
    assert is_observation_sink_active() is False


@pytest.mark.usefixtures("_enabled_env")
def test_init_with_keys_but_no_package_warns_and_stays_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "langfuse", None)

    with caplog.at_level(logging.WARNING):
        assert init_langfuse_tracing() is False

    assert is_observation_sink_active() is False
    assert "uv sync --extra langfuse" in caplog.text


@pytest.mark.usefixtures("_enabled_env")
def test_init_installs_sink_with_environment_release_and_masking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[dict[str, Any]] = []

    class _RecordingLangfuse:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

    import langfuse

    monkeypatch.setattr(langfuse, "Langfuse", _RecordingLangfuse)

    assert init_langfuse_tracing() is True
    assert init_langfuse_tracing() is True, "second call reuses the installed sink"

    assert is_observation_sink_active() is True
    (kwargs,) = created
    assert kwargs["public_key"] == "pk-lf-test"
    assert kwargs["base_url"] == "https://cloud.langfuse.com"
    assert kwargs["release"].startswith("opensre@")
    assert kwargs["environment"]
    assert kwargs["mask_otel_spans"] is mask_otel_spans


@pytest.mark.usefixtures("_enabled_env")
def test_init_survives_a_broken_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import langfuse

    def _explode(**_kwargs: Any) -> Any:
        raise RuntimeError("bad key format")

    monkeypatch.setattr(langfuse, "Langfuse", _explode)

    assert init_langfuse_tracing() is False
    assert is_observation_sink_active() is False
