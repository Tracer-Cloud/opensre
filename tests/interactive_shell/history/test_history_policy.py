"""Tests for history-redaction patterns, retention, and policy resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from surfaces.interactive_shell.prompt_history.policy import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_REDACTION_RULES,
    HistoryPolicy,
    RedactingFileHistory,
    RefreshingFileHistory,
    redact_text,
)


@pytest.mark.parametrize(
    ("raw", "expected_marker"),
    [
        ("AKIAIOSFODNN7EXAMPLE", "[REDACTED:aws_key]"),
        (
            "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY1",
            "[REDACTED:aws_secret]",
        ),
        ("ghp_" + "a" * 36, "[REDACTED:github_pat]"),
        ("github_pat_" + "x" * 82, "[REDACTED:github_pat]"),
        ("sk-ant-" + "y" * 90, "[REDACTED:anthropic_key]"),
        ("sk-" + "z" * 48, "[REDACTED:openai_key]"),
        ("xoxb-12345-67890-abcdefghijklmn", "[REDACTED:slack_token]"),
        ("sk_live_" + "Q" * 24, "[REDACTED:stripe_key]"),
        (
            "Authorization: Bearer abc123abc123abc123abc123",
            "Bearer [REDACTED]",
        ),
        (
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3OD.SflKxwRJSMeKKF2QT4",
            "[REDACTED:jwt]",
        ),
        ("psql --password=hunter2 -h db", "[REDACTED:password]"),
        (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEvQIBADANBgkq\n-----END RSA PRIVATE KEY-----",
            "[REDACTED:private_key]",
        ),
    ],
)
def test_each_default_pattern_redacts_known_token(raw: str, expected_marker: str) -> None:
    assert expected_marker in redact_text(raw)


def test_full_pem_block_is_redacted_including_body_and_footer() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQDaaaa\n"
        "bbbbbCCCCCdddddEEEEEfffffGGGGGhhhhhIIIIIjjjjjKKKKKlllll\n"
        "mmmmmNNNNNoooooPPPPPqqqqqRRRRRsssssTTTTTuuuuuVVVVVwwwww\n"
        "-----END RSA PRIVATE KEY-----"
    )
    redacted = redact_text(pem)
    assert "[REDACTED:private_key]" in redacted
    # The body and footer must be gone — no base64 or END marker can leak to disk.
    assert "MIIEvQIBADANBgkq" not in redacted
    assert "-----END" not in redacted
    assert "-----BEGIN" not in redacted


def test_openssh_pem_block_is_redacted_end_to_end() -> None:
    pem = (
        "-----BEGIN OPENSSH PRIVATE KEY-----\n"
        "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAACFw\n"
        "-----END OPENSSH PRIVATE KEY-----"
    )
    redacted = redact_text(pem)
    assert "[REDACTED:private_key]" in redacted
    assert "b3BlbnNzaC1rZXktdjEA" not in redacted


def test_pem_block_inside_history_entry_does_not_leak_to_disk(tmp_path: Path) -> None:
    history_file = tmp_path / "history"
    backend = RedactingFileHistory(str(history_file))
    pem = (
        "ssh-add - <<'EOF'\n"
        "-----BEGIN EC PRIVATE KEY-----\n"
        "MHcCAQEEIBcccDDDDDeeeeeFFFFFggggghhhhhIIIIIjjjjjKKKKKlllll\n"
        "mmmmmNNNNNoooooPPPPPqqqqq\n"
        "-----END EC PRIVATE KEY-----\n"
        "EOF"
    )
    backend.store_string(pem)
    contents = history_file.read_text(encoding="utf-8")
    assert "[REDACTED:private_key]" in contents
    assert "MHcCAQEEIBcccDDDDD" not in contents
    assert "-----END EC PRIVATE KEY-----" not in contents
    assert "-----BEGIN EC PRIVATE KEY-----" not in contents


def test_natural_language_is_left_alone() -> None:
    text = "investigate api errors after the redis cluster restarted at 12:30"
    assert redact_text(text) == text


def test_only_secret_segment_is_replaced() -> None:
    raw = "kubectl describe pod and AKIAIOSFODNN7EXAMPLE was in the env"
    out = redact_text(raw)
    assert "[REDACTED:aws_key]" in out
    assert out.startswith("kubectl describe pod and ")
    assert out.endswith(" was in the env")


def test_redacting_file_history_writes_redacted_only(tmp_path: Path) -> None:
    history_file = tmp_path / "history"
    backend = RedactingFileHistory(str(history_file))
    backend.store_string("AKIAIOSFODNN7EXAMPLE plus other tokens")
    contents = history_file.read_text(encoding="utf-8")
    assert "AKIAIOSFODNN7EXAMPLE" not in contents
    assert "[REDACTED:aws_key]" in contents


@pytest.mark.asyncio
@pytest.mark.parametrize("history_type", [RefreshingFileHistory, RedactingFileHistory])
async def test_file_history_reloads_entries_written_by_peer(
    tmp_path: Path,
    history_type: type[RefreshingFileHistory],
) -> None:
    history_file = tmp_path / "history"
    writer = history_type(str(history_file))
    reader = history_type(str(history_file))

    writer.store_string("first session command")
    assert [entry async for entry in reader.load()] == ["first session command"]

    writer.store_string("second session command")
    assert [entry async for entry in reader.load()] == [
        "second session command",
        "first session command",
    ]


def test_paused_backend_does_not_persist(tmp_path: Path) -> None:
    history_file = tmp_path / "history"
    backend = RedactingFileHistory(str(history_file))
    backend.paused = True
    backend.store_string("any string")
    assert not history_file.exists() or history_file.read_text(encoding="utf-8") == ""


def test_retention_cap_drops_oldest_entries(tmp_path: Path) -> None:
    history_file = tmp_path / "history"
    backend = RedactingFileHistory(str(history_file), max_entries=3)
    for i in range(5):
        backend.store_string(f"entry-{i}")

    persisted = list(reversed(list(backend.load_history_strings())))
    assert persisted == ["entry-2", "entry-3", "entry-4"]


def test_zero_retention_keeps_unlimited(tmp_path: Path) -> None:
    history_file = tmp_path / "history"
    backend = RedactingFileHistory(str(history_file), max_entries=0)
    for i in range(5):
        backend.store_string(f"entry-{i}")

    persisted = list(reversed(list(backend.load_history_strings())))
    assert persisted == [f"entry-{i}" for i in range(5)]


def test_policy_defaults_match_documented_shape() -> None:
    policy = HistoryPolicy.load()
    assert policy.enabled is True
    assert policy.redact is True
    assert policy.max_entries == DEFAULT_MAX_ENTRIES


def test_env_var_disables_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_HISTORY_ENABLED", "0")
    policy = HistoryPolicy.load()
    assert policy.enabled is False


def test_env_var_disables_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_HISTORY_REDACT", "false")
    policy = HistoryPolicy.load()
    assert policy.redact is False


def test_env_var_overrides_max_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_HISTORY_MAX_ENTRIES", "42")
    policy = HistoryPolicy.load()
    assert policy.max_entries == 42


def test_env_var_garbage_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENSRE_HISTORY_MAX_ENTRIES", "not-a-number")
    policy = HistoryPolicy.load()
    assert policy.max_entries == DEFAULT_MAX_ENTRIES


def test_file_settings_used_when_env_absent() -> None:
    policy = HistoryPolicy.load({"enabled": False, "redact": False, "max_entries": 100})
    assert policy.enabled is False
    assert policy.redact is False
    assert policy.max_entries == 100


def test_quoted_false_like_file_settings_are_parsed_as_disabled() -> None:
    policy = HistoryPolicy.load({"enabled": "false", "redact": "0"})
    assert policy.enabled is False
    assert policy.redact is False


def test_prune_to_cap_is_safe_when_cap_is_zero(tmp_path: Path) -> None:
    history_file = tmp_path / "history"
    backend = RedactingFileHistory(str(history_file), max_entries=0)
    backend.store_string("entry-0")
    backend._prune_to_cap()

    persisted = list(reversed(list(backend.load_history_strings())))
    assert persisted == ["entry-0"]


def test_default_pattern_set_size_is_stable() -> None:
    # Catch accidental rule deletions in PRs.
    assert len(DEFAULT_REDACTION_RULES) >= 12
