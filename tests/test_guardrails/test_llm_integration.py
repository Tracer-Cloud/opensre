"""Tests that guardrails intercept LLM client and chat node calls correctly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.guardrails.engine import GuardrailBlockedError, reset_guardrail_engine


def _write_rules(tmp_path: Path, rules: list[dict]) -> Path:
    config = tmp_path / "guardrails.yml"
    config.write_text(yaml.dump({"rules": rules}), encoding="utf-8")
    return config


@pytest.fixture(autouse=True)
def _reset_engine() -> None:
    """Reset the guardrail singleton before and after each test."""
    reset_guardrail_engine()
    yield  # type: ignore[misc]
    reset_guardrail_engine()


class TestLLMClientGuardrails:
    def test_redacts_before_api_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = _write_rules(tmp_path, [
            {"name": "aws_key", "action": "redact", "patterns": ["AKIA[0-9A-Z]{16}"]},
        ])
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        captured: dict = {}

        class _FakeMessages:
            @staticmethod
            def create(**kwargs: object) -> object:
                captured.update(kwargs)
                return type("R", (), {
                    "content": [type("B", (), {"type": "text", "text": "ok"})()],
                })()

        class _FakeClient:
            messages = _FakeMessages()

        from app.services.llm_client import LLMClient

        client = LLMClient(model="test", max_tokens=10)
        monkeypatch.setattr(client, "_client", _FakeClient())
        monkeypatch.setattr(client, "_ensure_client", lambda: None)

        client.invoke("My key is AKIAIOSFODNN7EXAMPLE")

        msg_content = captured["messages"][0]["content"]
        assert "AKIA" not in msg_content
        assert "[REDACTED:aws_key]" in msg_content

    def test_blocks_and_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = _write_rules(tmp_path, [
            {"name": "blocker", "action": "block", "keywords": ["forbidden"]},
        ])
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        from app.services.llm_client import LLMClient

        client = LLMClient(model="test", max_tokens=10)
        monkeypatch.setattr(client, "_ensure_client", lambda: None)

        with pytest.raises(GuardrailBlockedError, match="blocker"):
            client.invoke("this is forbidden")

    def test_passthrough_when_no_rules(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.guardrails.engine.get_default_rules_path",
            lambda: tmp_path / "missing.yml",
        )

        captured: dict = {}

        class _FakeMessages:
            @staticmethod
            def create(**kwargs: object) -> object:
                captured.update(kwargs)
                return type("R", (), {
                    "content": [type("B", (), {"type": "text", "text": "ok"})()],
                })()

        class _FakeClient:
            messages = _FakeMessages()

        from app.services.llm_client import LLMClient

        client = LLMClient(model="test", max_tokens=10)
        monkeypatch.setattr(client, "_client", _FakeClient())
        monkeypatch.setattr(client, "_ensure_client", lambda: None)

        client.invoke("AKIAIOSFODNN7EXAMPLE")

        msg_content = captured["messages"][0]["content"]
        assert "AKIAIOSFODNN7EXAMPLE" in msg_content


class TestOpenAIClientGuardrails:
    def test_redacts_before_api_call(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = _write_rules(tmp_path, [
            {"name": "aws_key", "action": "redact", "patterns": ["AKIA[0-9A-Z]{16}"]},
        ])
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        captured: dict = {}

        class _FakeCompletions:
            @staticmethod
            def create(**kwargs: object) -> object:
                captured.update(kwargs)
                return type("R", (), {
                    "choices": [type("C", (), {
                        "message": type("M", (), {"content": "ok"})(),
                    })()],
                })()

        class _FakeChat:
            completions = _FakeCompletions()

        class _FakeClient:
            chat = _FakeChat()

        from app.services.llm_client import OpenAILLMClient

        client = OpenAILLMClient(model="test", max_tokens=10, api_key_env="TEST_KEY")
        monkeypatch.setenv("TEST_KEY", "fake-key")
        monkeypatch.setattr(client, "_client", _FakeClient())
        monkeypatch.setattr(client, "_ensure_client", lambda: None)

        client.invoke("My key is AKIAIOSFODNN7EXAMPLE")

        msg_content = captured["messages"][0]["content"]
        assert "AKIA" not in msg_content
        assert "[REDACTED:aws_key]" in msg_content


class _FakeMessage:
    """Minimal LangChain-style message object with mutable content."""

    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content: str | None = content
        self.type = msg_type


class TestChatNodeGuardrails:
    def test_redacts_message_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = _write_rules(tmp_path, [
            {"name": "aws_key", "action": "redact", "patterns": ["AKIA[0-9A-Z]{16}"]},
        ])
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        from app.nodes.chat import _apply_guardrails_to_messages

        msgs: list[Any] = [
            _FakeMessage("hello"),
            _FakeMessage("key is AKIAIOSFODNN7EXAMPLE"),
        ]
        result = _apply_guardrails_to_messages(msgs)

        assert result[0].content == "hello"
        assert "AKIA" not in str(result[1].content)
        assert "[REDACTED:aws_key]" in str(result[1].content)
        # Original should be untouched
        assert msgs[1].content == "key is AKIAIOSFODNN7EXAMPLE"

    def test_blocks_on_chat_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = _write_rules(tmp_path, [
            {"name": "blocker", "action": "block", "keywords": ["forbidden"]},
        ])
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        from app.nodes.chat import _apply_guardrails_to_messages

        msgs: list[Any] = [_FakeMessage("this is forbidden")]
        with pytest.raises(GuardrailBlockedError):
            _apply_guardrails_to_messages(msgs)

    def test_skips_non_string_content(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = _write_rules(tmp_path, [
            {"name": "r1", "action": "redact", "keywords": ["secret"]},
        ])
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        from app.nodes.chat import _apply_guardrails_to_messages

        msg = _FakeMessage("")
        msg.content = None
        msgs: list[Any] = [msg]
        _apply_guardrails_to_messages(msgs)

    def test_noop_when_no_rules(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "app.guardrails.engine.get_default_rules_path",
            lambda: tmp_path / "missing.yml",
        )

        from app.nodes.chat import _apply_guardrails_to_messages

        msgs: list[Any] = [_FakeMessage("AKIAIOSFODNN7EXAMPLE")]
        result = _apply_guardrails_to_messages(msgs)
        assert result[0].content == "AKIAIOSFODNN7EXAMPLE"
