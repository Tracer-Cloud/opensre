"""StructuredOutputClient native path is opt-in (default off)."""

from __future__ import annotations

import json
import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel, Field
from pydantic.networks import IPvAnyAddress

from config.constants.llm import OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV
from config.constants.masking import (
    OPENSRE_MASK_ENABLED_ENV,
    OPENSRE_MASK_EXTRA_REGEX_ENV,
    OPENSRE_MASK_KINDS_ENV,
)
from core.llm.shared.structured_output import (
    StructuredOutputClient,
    json_schema_for_structured_output,
    native_structured_output_enabled,
)
from integrations.llm_cli.runner import CLIBackedLLMClient


class _TinySchema(BaseModel):
    root_cause_category: str = Field(description="category")
    note: str = ""


class _NativeBase:
    def __init__(self) -> None:
        self.prompt_invokes = 0
        self.native_invokes = 0

    def invoke_structured(self, model: type[BaseModel], _prompt: str) -> BaseModel:
        self.native_invokes += 1
        return model.model_validate({"root_cause_category": "pod_oomkilled", "note": "native"})

    def invoke(self, _prompt: str) -> Any:
        self.prompt_invokes += 1

        class _Resp:
            content = '{"root_cause_category": "code_defect", "note": "prompt"}'

        return _Resp()


class _FailingNativeBase:
    def __init__(self) -> None:
        self.native_invokes = 0

    def invoke_structured(self, _model: type[BaseModel], _prompt: str) -> BaseModel:
        self.native_invokes += 1
        raise RuntimeError("provider rejected output_config")

    def invoke(self, _prompt: str) -> Any:
        class _Resp:
            content = '{"root_cause_category": "code_defect", "note": "fallback"}'

        return _Resp()


def test_json_schema_sets_additional_properties_false() -> None:
    schema = json_schema_for_structured_output(_TinySchema)
    assert schema.get("additionalProperties") is False


def test_native_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV, raising=False)
    assert native_structured_output_enabled() is False
    base = _NativeBase()
    result = StructuredOutputClient(base, _TinySchema).invoke("extract")
    assert result.note == "prompt"
    assert base.native_invokes == 0
    assert base.prompt_invokes == 1


def test_structured_output_uses_native_when_opted_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV, "1")
    base = _NativeBase()
    result = StructuredOutputClient(base, _TinySchema).invoke("extract")
    assert result.root_cause_category == "pod_oomkilled"
    assert result.note == "native"
    assert base.native_invokes == 1
    assert base.prompt_invokes == 0


def test_structured_output_falls_back_when_native_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OPENSRE_LLM_NATIVE_STRUCTURED_OUTPUT_ENV, "1")
    base = _FailingNativeBase()
    result = StructuredOutputClient(base, _TinySchema).invoke("extract")
    assert result.root_cause_category == "code_defect"
    assert result.note == "fallback"
    assert base.native_invokes == 1


class _HostResult(BaseModel):
    host: IPvAnyAddress


@patch("integrations.llm_cli.runner.subprocess.run")
def test_structured_output_unmasks_typed_field_before_validate(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Typed fields (e.g. IPvAnyAddress) must receive real values, not placeholders.

    Regression for issue #6714 — structured output path. The LLM echoes the
    masked placeholder; StructuredOutputClient must unmask before model_validate
    so Pydantic validation sees '10.0.1.42', not '<IP_ADDRESS_0>'.
    """
    real_ip = "10.0.1.42"
    monkeypatch.setenv(OPENSRE_MASK_ENABLED_ENV, "true")
    monkeypatch.setenv(OPENSRE_MASK_KINDS_ENV, "ip_address")

    mock_adapter = MagicMock()
    mock_adapter.name = "mock-cli"
    mock_adapter.detect.return_value = MagicMock(
        installed=True, bin_path="/usr/bin/mock", logged_in=True, detail="ok"
    )
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/mock",), stdin=None, cwd="/tmp", env=None, timeout_sec=30.0
    )
    mock_adapter.parse.side_effect = lambda *, stdout, **_kwargs: stdout.strip()
    mock_run.return_value = MagicMock(
        returncode=0, stdout='{"host": "<IP_ADDRESS_0>"}\n', stderr=""
    )

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        cli = CLIBackedLLMClient(mock_adapter)
        result = StructuredOutputClient(cli, _HostResult).invoke(f"check host {real_ip}")

    assert str(result.host) == real_ip


class _AnswerResult(BaseModel):
    answer: str


@patch("integrations.llm_cli.runner.subprocess.run")
def test_structured_output_restores_quoted_value(
    mock_run: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restore quoted values without corrupting the structured response.

    Regression for the JSON round-trip bug: json.loads(masking.unmask(json.dumps()))
    breaks when an unmasked value contains '"'. Restore parsed values individually.
    """
    quoted_value = 'ab"cd'
    # Use a custom extra-regex label so the placeholder is deterministic: <QUOTED_VAL_0>
    placeholder = "<QUOTED_VAL_0>"
    extra_patterns = json.dumps({"QUOTED_VAL": re.escape(quoted_value)})

    monkeypatch.setenv(OPENSRE_MASK_ENABLED_ENV, "true")
    monkeypatch.setenv(OPENSRE_MASK_EXTRA_REGEX_ENV, extra_patterns)

    mock_adapter = MagicMock()
    mock_adapter.name = "mock-cli"
    mock_adapter.detect.return_value = MagicMock(
        installed=True, bin_path="/usr/bin/mock", logged_in=True, detail="ok"
    )
    mock_adapter.build.return_value = MagicMock(
        argv=("/usr/bin/mock",), stdin=None, cwd="/tmp", env=None, timeout_sec=30.0
    )
    mock_adapter.parse.side_effect = lambda *, stdout, **_kwargs: stdout.strip()
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=f'{{"answer": "{placeholder}"}}\n',
        stderr="",
    )

    with patch("infrastructure.safety.guardrails.evaluator.get_guardrail_evaluator") as gr:
        gr.return_value.is_active = False
        cli = CLIBackedLLMClient(mock_adapter)
        result = StructuredOutputClient(cli, _AnswerResult).invoke(
            f"what is the answer to {quoted_value}"
        )

    assert result.answer == quoted_value
