"""Tests that guardrails intercept LLM client and chat node calls correctly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.guardrails.engine import GuardrailBlockedError, reset_guardrail_engine

# ---------------------------------------------------------------------------
# Shared LLM-client capture fixtures
# ---------------------------------------------------------------------------
# ``TestOverlappingRedactionReachesDownstream`` (and the simpler tests on
# this module) need a fake LLM client whose ``messages.create`` /
# ``chat.completions.create`` records the kwargs it was called with so the
# test can assert on the payload that *would* have been sent over the wire.
# These fixtures replace the four ad-hoc inner-class definitions that were
# previously duplicated verbatim across each test (per @muddlebee's PR #780
# review nit).


def _anthropic_fake_response() -> object:
    """Smallest possible Anthropic-shaped response object."""
    return type("R", (), {"content": [type("B", (), {"type": "text", "text": "ok"})()]})()


def _openai_fake_response() -> object:
    """Smallest possible OpenAI-shaped chat-completion response object."""
    return type(
        "R",
        (),
        {"choices": [type("C", (), {"message": type("M", (), {"content": "ok"})()})()]},
    )()


@pytest.fixture
def anthropic_capture(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
    """Patch ``LLMClient`` (Anthropic surface) to capture the kwargs it would
    have sent to ``messages.create``. Returns ``(client, captured)`` where
    ``captured`` is a dict populated on each ``client.invoke(...)`` call."""
    captured: dict[str, Any] = {}

    class _FakeMessages:
        @staticmethod
        def create(**kwargs: object) -> object:
            captured.update(kwargs)
            return _anthropic_fake_response()

    class _FakeClient:
        messages = _FakeMessages()

    from app.services.llm_client import LLMClient

    client = LLMClient(model="test", max_tokens=10)
    monkeypatch.setattr(client, "_client", _FakeClient())
    monkeypatch.setattr(client, "_ensure_client", lambda: None)
    return client, captured


@pytest.fixture
def openai_capture(monkeypatch: pytest.MonkeyPatch) -> tuple[Any, dict[str, Any]]:
    """Patch ``OpenAILLMClient`` (OpenAI surface) to capture the kwargs it
    would have sent to ``chat.completions.create``. Returns
    ``(client, captured)``."""
    captured: dict[str, Any] = {}

    class _FakeCompletions:
        @staticmethod
        def create(**kwargs: object) -> object:
            captured.update(kwargs)
            return _openai_fake_response()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    from app.services.llm_client import OpenAILLMClient

    monkeypatch.setenv("TEST_KEY", "fake-key")
    client = OpenAILLMClient(model="test", max_tokens=10, api_key_env="TEST_KEY")
    monkeypatch.setattr(client, "_client", _FakeClient())
    monkeypatch.setattr(client, "_ensure_client", lambda: None)
    return client, captured


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
        config = _write_rules(
            tmp_path,
            [
                {"name": "aws_key", "action": "redact", "patterns": ["AKIA[0-9A-Z]{16}"]},
            ],
        )
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        captured: dict = {}

        class _FakeMessages:
            @staticmethod
            def create(**kwargs: object) -> object:
                captured.update(kwargs)
                return type(
                    "R",
                    (),
                    {
                        "content": [type("B", (), {"type": "text", "text": "ok"})()],
                    },
                )()

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
        config = _write_rules(
            tmp_path,
            [
                {"name": "blocker", "action": "block", "keywords": ["forbidden"]},
            ],
        )
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        from app.services.llm_client import LLMClient

        client = LLMClient(model="test", max_tokens=10)
        monkeypatch.setattr(client, "_ensure_client", lambda: None)

        with pytest.raises(GuardrailBlockedError, match="blocker"):
            client.invoke("this is forbidden")

    def test_passthrough_when_no_rules(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.guardrails.engine.get_default_rules_path",
            lambda: tmp_path / "missing.yml",
        )

        captured: dict = {}

        class _FakeMessages:
            @staticmethod
            def create(**kwargs: object) -> object:
                captured.update(kwargs)
                return type(
                    "R",
                    (),
                    {
                        "content": [type("B", (), {"type": "text", "text": "ok"})()],
                    },
                )()

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
        config = _write_rules(
            tmp_path,
            [
                {"name": "aws_key", "action": "redact", "patterns": ["AKIA[0-9A-Z]{16}"]},
            ],
        )
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        captured: dict = {}

        class _FakeCompletions:
            @staticmethod
            def create(**kwargs: object) -> object:
                captured.update(kwargs)
                return type(
                    "R",
                    (),
                    {
                        "choices": [
                            type(
                                "C",
                                (),
                                {
                                    "message": type("M", (), {"content": "ok"})(),
                                },
                            )()
                        ],
                    },
                )()

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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = _write_rules(
            tmp_path,
            [
                {"name": "aws_key", "action": "redact", "patterns": ["AKIA[0-9A-Z]{16}"]},
            ],
        )
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
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = _write_rules(
            tmp_path,
            [
                {"name": "blocker", "action": "block", "keywords": ["forbidden"]},
            ],
        )
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        from app.nodes.chat import _apply_guardrails_to_messages

        msgs: list[Any] = [_FakeMessage("this is forbidden")]
        with pytest.raises(GuardrailBlockedError):
            _apply_guardrails_to_messages(msgs)

    def test_skips_non_string_content(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        config = _write_rules(
            tmp_path,
            [
                {"name": "r1", "action": "redact", "keywords": ["secret"]},
            ],
        )
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

        from app.nodes.chat import _apply_guardrails_to_messages

        msg = _FakeMessage("")
        msg.content = None
        msgs: list[Any] = [msg]
        _apply_guardrails_to_messages(msgs)

    def test_noop_when_no_rules(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "app.guardrails.engine.get_default_rules_path",
            lambda: tmp_path / "missing.yml",
        )

        from app.nodes.chat import _apply_guardrails_to_messages

        msgs: list[Any] = [_FakeMessage("AKIAIOSFODNN7EXAMPLE")]
        result = _apply_guardrails_to_messages(msgs)
        assert result[0].content == "AKIAIOSFODNN7EXAMPLE"


# Production-grade configs exercising every reachable overlap shape the fix
# must handle. ``aws_access_key`` is a strict substring of
# ``generic_api_token`` when the token value is itself an AWS key, so a
# real investigation that quotes ``api_key=AKIA...`` from a source file
# triggers the contained-span path that main corrupts.
_OVERLAPPING_RULES: list[dict] = [
    {
        "name": "aws_access_key",
        "action": "redact",
        "patterns": [r"(?:AKIA|ASIA)[A-Z0-9]{16}"],
    },
    {
        "name": "generic_api_token",
        "action": "redact",
        "patterns": [
            r"(?i)(?:api_key|api_token|auth_token|access_token|secret_key)"
            r"[\s=:]+[A-Za-z0-9_\-]{20,}"
        ],
    },
]


class TestOverlappingRedactionReachesDownstream:
    """End-to-end: every LLM client and the chat node must dispatch
    fully-redacted content downstream when the shipped-style overlapping
    rules fire. These tests close the gap between the unit-level engine
    tests and the real call sites; a regression in the merge algorithm
    would be caught here even if engine tests were skipped."""

    def _install_rules(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        config = _write_rules(tmp_path, _OVERLAPPING_RULES)
        monkeypatch.setattr("app.guardrails.engine.get_default_rules_path", lambda: config)
        monkeypatch.setattr("app.guardrails.rules.get_default_rules_path", lambda: config)

    def test_anthropic_client_sends_merged_redaction(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_capture: tuple[Any, dict[str, Any]],
    ) -> None:
        """``AnthropicLLMClient.invoke`` must redact the full overlapping
        span before the payload reaches ``Anthropic.messages.create``."""
        self._install_rules(tmp_path, monkeypatch)
        client, captured = anthropic_capture

        client.invoke("Debug dump: api_key=AKIAIOSFODNN7EXAMPLE from config.yml")

        content = captured["messages"][0]["content"]
        # Full span merged — neither the label nor the key value leaks.
        assert "api_key=" not in content
        assert "AKIA" not in content
        assert "IOSFODNN7EXAMPLE" not in content
        # Representative rule is the wider one (generic_api_token).
        assert "[REDACTED:generic_api_token]" in content
        assert "[REDACTED:aws_access_key]" not in content

    def test_anthropic_system_prompt_also_redacted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_capture: tuple[Any, dict[str, Any]],
    ) -> None:
        """System prompt path (distinct from messages) must also get the
        merged-redaction treatment, and Anthropic's client must hoist the
        ``role: system`` entry into the top-level ``system`` kwarg rather
        than leaving it embedded in the messages list."""
        self._install_rules(tmp_path, monkeypatch)
        client, captured = anthropic_capture

        system = "Operator note: api_key=AKIAIOSFODNN7EXAMPLE must remain private."
        client.invoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": "ok"},
            ]
        )

        # Subscript (not ``.get``) so a missing ``system`` kwarg surfaces a
        # KeyError immediately — the whole point of the test is that the
        # client hoisted the system entry out of the messages list.
        system_kwarg = captured["system"]
        assert system_kwarg, "system kwarg must be non-empty after redaction"
        assert "api_key=" not in system_kwarg
        assert "AKIA" not in system_kwarg
        assert "[REDACTED:generic_api_token]" in system_kwarg
        # System must be a separate kwarg, not smuggled into ``messages``.
        for msg in captured.get("messages", []):
            assert msg.get("role") != "system", f"system role leaked into messages list: {msg!r}"

    def test_openai_client_sends_merged_redaction(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        openai_capture: tuple[Any, dict[str, Any]],
    ) -> None:
        """``OpenAILLMClient.invoke`` must redact the full overlapping span
        before dispatching to ``chat.completions.create``."""
        self._install_rules(tmp_path, monkeypatch)
        client, captured = openai_capture

        client.invoke("Investigation: api_key=AKIAIOSFODNN7EXAMPLE surfaced in logs")

        content = captured["messages"][0]["content"]
        assert "api_key=" not in content
        assert "AKIA" not in content
        assert "[REDACTED:generic_api_token]" in content

    def test_chat_node_emits_merged_redaction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The LangGraph chat node must mirror the LLM-client behavior on
        overlapping rules, leaving originals untouched (it copies)."""
        self._install_rules(tmp_path, monkeypatch)

        from app.nodes.chat import _apply_guardrails_to_messages

        original = "Investigation: api_key=AKIAIOSFODNN7EXAMPLE surfaced in logs"
        msgs: list[Any] = [_FakeMessage(original)]
        result = _apply_guardrails_to_messages(msgs)

        redacted = str(result[0].content)
        assert "api_key=" not in redacted
        assert "AKIA" not in redacted
        assert "[REDACTED:generic_api_token]" in redacted
        # Source message untouched — confirms the defensive copy.
        assert msgs[0].content == original

    def test_contained_real_secret_fully_redacted_in_pipeline(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        anthropic_capture: tuple[Any, dict[str, Any]],
    ) -> None:
        """The core regression: the pre-fix output leaked sensitive
        bookends (``api_key=`` and the key value's suffix). An end-to-end
        assertion that the final payload contains *no* fragment of either
        the label prefix or the key value."""
        self._install_rules(tmp_path, monkeypatch)
        client, captured = anthropic_capture

        client.invoke("Leak test: api_key=AKIAIOSFODNN7EXAMPLE tail goes here")

        content = captured["messages"][0]["content"]
        for fragment in (
            "api_key=",  # ← pre-fix leaked this
            "AKIA",  # sensitive marker
            "IOSFODNN",  # sensitive tail
            "7EXAMPLE",  # sensitive suffix
        ):
            assert fragment not in content, (
                f"fragment {fragment!r} leaked into downstream payload: {content!r}"
            )
