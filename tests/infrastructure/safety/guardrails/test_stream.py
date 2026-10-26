"""Tests for infrastructure/safety/guardrails/stream.py.

The acceptance criteria from issue #1499 map onto these tests as follows:

* "No false negatives when a secret straddles a chunk boundary"
    -> test_secret_split_across_chunk_boundary_is_redacted
    -> test_secret_split_across_three_chunks_is_redacted

* "Adds agent_secret_detected event"
    -> test_match_emits_agent_secret_detected_event
    -> test_no_match_does_not_emit_event

* "Redacted version returned to caller; original quarantined for audit"
    -> test_match_returns_redacted_text
    -> test_audit_logger_receives_each_match
    -> test_block_action_is_redacted_not_raised
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from infrastructure.safety.guardrails.evaluator import GuardrailEvaluator
from infrastructure.safety.guardrails.rules import GuardrailAction, GuardrailRule
from infrastructure.safety.guardrails.stream import GuardrailStream

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule(
    name: str = "aws_key",
    action: GuardrailAction = GuardrailAction.REDACT,
    patterns: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
    replacement: str = "",
) -> GuardrailRule:
    """Mirror tests/infrastructure/safety/guardrails/test_engine.py's helper. Kept local so the
    tests do not import the engine test module."""
    compiled = tuple(re.compile(p, re.IGNORECASE) for p in patterns)
    return GuardrailRule(
        name=name,
        action=action,
        patterns=compiled,
        keywords=tuple(k.lower() for k in keywords),
        replacement=replacement,
    )


def _evaluator_for_aws_keys() -> GuardrailEvaluator:
    return GuardrailEvaluator([_rule(patterns=("AKIA[0-9A-Z]{16}",))])


@pytest.fixture
def stub_capture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture analytics calls without touching the real provider."""
    calls: list[dict[str, Any]] = []

    def _stub(*, rule_names: tuple[str, ...], count: int, blocked: bool) -> None:
        calls.append({"rule_names": rule_names, "count": count, "blocked": blocked})

    monkeypatch.setattr(
        "infrastructure.safety.guardrails.stream.capture_agent_secret_detected", _stub
    )
    return calls


# ---------------------------------------------------------------------------
# Boundary buffering: the headline acceptance criterion
# ---------------------------------------------------------------------------


def test_secret_split_across_chunk_boundary_is_redacted(
    stub_capture: list[dict[str, Any]],
) -> None:
    """An AWS key whose bytes straddle two stdout reads must still be detected.

    Without buffering, a naive per-chunk scan would see ``AKIAIOSF`` and
    ``ODNN7EXAMPLE`` separately and miss both. The stream buffers up to a
    newline boundary so the engine sees the joined window.
    """
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator)

    # First chunk: leading half of the secret, no newline -> stays buffered.
    out1 = stream.feed("env AKIAIOSF")
    assert out1 == ""

    # Second chunk: trailing half plus newline. Now the joined window holds
    # the full key, the engine matches, and we emit a redacted line.
    out2 = stream.feed("ODNN7EXAMPLE end\n")

    assert "AKIAIOSFODNN7EXAMPLE" not in out2
    assert "[REDACTED:aws_key]" in out2
    # The non-secret bytes around the redaction survive intact.
    assert out2.startswith("env ")
    assert out2.endswith("end\n")
    # One emit covering both halves of the key.
    assert len(stub_capture) == 1


def test_secret_split_across_three_chunks_is_redacted(
    stub_capture: list[dict[str, Any]],
) -> None:
    """Worst-case fragmentation: three reads, none of which match alone."""
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator)

    assert stream.feed("AKIA") == ""
    assert stream.feed("IOSFODNN") == ""
    out = stream.feed("7EXAMPLE\n")

    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:aws_key]" in out


def test_text_without_newline_stays_buffered_until_flush(
    stub_capture: list[dict[str, Any]],
) -> None:
    """A trailing line with no terminating newline must not be silently dropped."""
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator)

    assert stream.feed("AKIAIOSFODNN7EXAMPLE no-newline") == ""

    flushed = stream.flush()
    assert "[REDACTED:aws_key]" in flushed
    assert "no-newline" in flushed


def test_buffer_force_flushes_at_max_chunk_len(
    stub_capture: list[dict[str, Any]],
) -> None:
    """A no-newline stream must not grow without bound. Once the buffer hits
    ``max_chunk_len`` characters the wrapper force-flushes to disk."""
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator, max_chunk_len=64)

    # Pad out the buffer just below the threshold without a newline.
    padding = "x" * 60
    assert stream.feed(padding) == ""

    # This pushes past max_chunk_len with a secret embedded; the wrapper
    # force-flushes the joined buffer in one go.
    out = stream.feed(" AKIAIOSFODNN7EXAMPLE")
    assert "[REDACTED:aws_key]" in out
    # Buffer cleared so a follow-up clean line is unaffected.
    assert stream.feed("clean\n") == "clean\n"


# ---------------------------------------------------------------------------
# Plain redaction + analytics
# ---------------------------------------------------------------------------


def test_match_returns_redacted_text(stub_capture: list[dict[str, Any]]) -> None:
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator)

    out = stream.feed("export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n")

    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:aws_key]" in out
    assert out.startswith("export AWS_ACCESS_KEY_ID=")


def test_no_match_passes_through_unchanged(stub_capture: list[dict[str, Any]]) -> None:
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator)

    out = stream.feed("nothing sensitive here\n")
    assert out == "nothing sensitive here\n"
    assert stub_capture == []


def test_inactive_evaluator_passes_through_unchanged(
    stub_capture: list[dict[str, Any]],
) -> None:
    """Evaluator with no rules: stream is a passthrough so users who haven't
    configured guardrails do not pay any per-chunk overhead beyond buffering."""
    stream = GuardrailStream(GuardrailEvaluator([]))
    out = stream.feed("export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n")
    assert out == "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
    assert stub_capture == []


def test_match_emits_agent_secret_detected_event(
    stub_capture: list[dict[str, Any]],
) -> None:
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator)

    stream.feed("AKIAIOSFODNN7EXAMPLE\n")

    assert len(stub_capture) == 1
    event = stub_capture[0]
    assert event["rule_names"] == ("aws_key",)
    assert event["count"] == 1
    assert event["blocked"] is False


def test_two_chunks_two_secrets_emit_two_events(
    stub_capture: list[dict[str, Any]],
) -> None:
    """Each flushed chunk that matched gets one event. Two separate flushes
    therefore produce two events even if they trip the same rule."""
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator)

    stream.feed("AKIAIOSFODNN7EXAMPLE\n")
    stream.feed("AKIAABCDEFGHIJKLMNOP\n")

    assert len(stub_capture) == 2


# ---------------------------------------------------------------------------
# Audit + BLOCK promotion
# ---------------------------------------------------------------------------


def test_audit_logger_receives_each_match(stub_capture: list[dict[str, Any]]) -> None:
    """Original (non-redacted) match text must be quarantined for forensic audit."""
    evaluator = _evaluator_for_aws_keys()
    audit = MagicMock()
    stream = GuardrailStream(evaluator, audit_logger=audit)

    stream.feed("AKIAIOSFODNN7EXAMPLE\n")

    assert audit.log.call_count == 1
    kwargs = audit.log.call_args.kwargs
    assert kwargs["rule_name"] == "aws_key"
    assert kwargs["matched_text_preview"] == "AKIAIOSFODNN7EXAMPLE"


def test_audit_logger_optional(stub_capture: list[dict[str, Any]]) -> None:
    """Stream works without an audit logger (useful for tests and lightweight callers)."""
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator, audit_logger=None)

    out = stream.feed("AKIAIOSFODNN7EXAMPLE\n")
    assert "[REDACTED:aws_key]" in out


def test_flush_on_empty_buffer_returns_empty(
    stub_capture: list[dict[str, Any]],
) -> None:
    """Idempotent flush — calling it twice should not double-emit anything."""
    evaluator = _evaluator_for_aws_keys()
    stream = GuardrailStream(evaluator)

    assert stream.flush() == ""
    stream.feed("clean line\n")
    assert stream.flush() == ""


def test_overlapping_matches_merge_into_single_redaction(
    stub_capture: list[dict[str, Any]],
) -> None:
    """Two rules that overlap on the same span must produce one redaction, not two
    nested ones. Mirrors GuardrailEvaluator._redact's longest-source-wins behavior."""
    # Two rules that hit the same string ``super_secret_token``. The wider
    # match wins on rule-name selection.
    evaluator = GuardrailEvaluator(
        [
            _rule(name="narrow", patterns=("secret_token",)),
            _rule(name="wide", patterns=("super_secret_token_value",)),
        ]
    )
    stream = GuardrailStream(evaluator)

    out = stream.feed("found super_secret_token_value here\n")

    assert "secret" not in out
    assert "super_" not in out
    assert "_value" not in out
    # Wider match wins; output contains exactly one redaction marker.
    assert out.count("[REDACTED:") == 1
    assert "[REDACTED:wide]" in out


def test_audit_action_is_logged_but_not_redacted(
    stub_capture: list[dict[str, Any]],
) -> None:
    """AUDIT means 'log only' in the engine. Streaming must not redact AUDIT
    matches because that would silently censor text that the LLM path passes
    through unchanged. Engine parity matters here so a team running AUDIT
    rules to observe agent behaviour gets the same output in both paths.
    """
    evaluator = GuardrailEvaluator(
        [_rule(name="watch_passwords", action=GuardrailAction.AUDIT, keywords=("password",))]
    )
    audit = MagicMock()
    stream = GuardrailStream(evaluator, audit_logger=audit)

    out = stream.feed("user typed password=hunter2\n")

    # Original text passes through untouched.
    assert out == "user typed password=hunter2\n"
    # But the audit logger still receives the match for forensic purposes.
    assert audit.log.call_count == 1
    assert audit.log.call_args.kwargs["rule_name"] == "watch_passwords"
    # And the analytics event still fires so dashboards can count detections.
    assert len(stub_capture) == 1


def test_audit_match_alongside_redact_match_only_redacts_redact(
    stub_capture: list[dict[str, Any]],
) -> None:
    """A chunk that trips both an AUDIT and a REDACT rule must redact only
    the REDACT span and leave the AUDIT span visible. The audit logger must
    still receive both matches so the AUDIT rule's forensic intent is
    preserved even when the chunk also contained a REDACT-action secret."""
    evaluator = GuardrailEvaluator(
        [
            _rule(name="aws_key", action=GuardrailAction.REDACT, patterns=("AKIA[0-9A-Z]{16}",)),
            _rule(name="watch_email", action=GuardrailAction.AUDIT, keywords=("@example.com",)),
        ]
    )
    audit = MagicMock()
    stream = GuardrailStream(evaluator, audit_logger=audit)

    out = stream.feed("user=alice@example.com key=AKIAIOSFODNN7EXAMPLE\n")

    # AUDIT match stays visible.
    assert "alice@example.com" in out
    # REDACT match is replaced.
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:aws_key]" in out
    # Both matches reached the audit logger (REDACT and AUDIT).
    assert audit.log.call_count == 2
    audited_rules = {call.kwargs["rule_name"] for call in audit.log.call_args_list}
    assert audited_rules == {"aws_key", "watch_email"}


def test_custom_rule_replacement_is_honored(
    stub_capture: list[dict[str, Any]],
) -> None:
    """Rules can configure a custom ``replacement`` string (e.g. ``[PII]``).
    The streaming path must use that string, mirroring the engine's
    :meth:`_get_replacement` behaviour, so the same secret is replaced
    identically in LLM and stdout output."""
    evaluator = GuardrailEvaluator(
        [
            _rule(
                name="email",
                action=GuardrailAction.REDACT,
                patterns=(r"[\w.+-]+@[\w-]+\.[\w.-]+",),
                replacement="[PII]",
            )
        ]
    )
    stream = GuardrailStream(evaluator)

    out = stream.feed("user contact alice@example.com here\n")

    assert "alice@example.com" not in out
    # The custom replacement wins. The default [REDACTED:email] must NOT appear.
    assert "[PII]" in out
    assert "[REDACTED:" not in out


def test_block_action_is_redacted_not_raised(
    stub_capture: list[dict[str, Any]],
) -> None:
    """In the LLM input path BLOCK raises GuardrailBlockedError. In the stdout
    streaming path raising would silently truncate the agent's output, so the
    wrapper promotes BLOCK to REDACT and reports the BLOCK status via the
    analytics event instead."""
    evaluator = GuardrailEvaluator(
        [_rule(name="forbidden", action=GuardrailAction.BLOCK, keywords=("rm -rf /",))]
    )
    stream = GuardrailStream(evaluator)

    out = stream.feed("about to run rm -rf / on host\n")

    # No exception, no truncation, secret-equivalent text is redacted.
    assert "rm -rf /" not in out
    assert "[REDACTED:forbidden]" in out
    # Telemetry preserved the original action label so the dashboard can
    # show that a BLOCK rule fired even though we did not actually block.
    assert stub_capture[0]["blocked"] is True
