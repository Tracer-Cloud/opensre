from __future__ import annotations

from config.env_assignment import env_assignment_key


def test_env_assignment_key_reads_plain_assignments() -> None:
    assert env_assignment_key("FOO=bar\n") == "FOO"
    assert env_assignment_key("  GITLAB_BASE_URL = https://example\n") == "GITLAB_BASE_URL"


def test_env_assignment_key_reads_export_prefixed_assignments() -> None:
    """Shell-sourced env files write ``export KEY=value``; that is still an assignment."""
    assert env_assignment_key("export TELEGRAM_BOT_TOKEN=secret\n") == "TELEGRAM_BOT_TOKEN"
    assert env_assignment_key("export  DD_SITE=datadoghq.com\n") == "DD_SITE"


def test_env_assignment_key_does_not_treat_exportable_as_a_prefix() -> None:
    assert env_assignment_key("exportable=1\n") == "exportable"


def test_env_assignment_key_ignores_comments_and_blank_lines() -> None:
    assert env_assignment_key("# FOO=bar\n") is None
    assert env_assignment_key("\n") is None
    assert env_assignment_key("just text\n") is None
