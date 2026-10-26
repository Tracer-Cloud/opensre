from __future__ import annotations

from config.prompt_log import PromptLogConfig


def _no_file_settings(monkeypatch) -> None:
    monkeypatch.setattr(
        "config.prompt_log.read_prompt_log_settings",
        lambda: {},
    )


def test_load_defaults_to_redact_on(monkeypatch) -> None:
    """Redaction must default on, matching HistoryPolicy — see issue #2804.

    Prompt/response content can carry the same token shapes as typed command
    history and additionally leaves the machine via the PostHog sink, so it
    must not ship less guarded than history by default.
    """
    _no_file_settings(monkeypatch)
    for var in (
        "OPENSRE_PROMPT_LOG_DISABLED",
        "OPENSRE_PROMPT_LOG_LOCAL_DISABLED",
        "OPENSRE_PROMPT_LOG_REDACT",
        "OPENSRE_PROMPT_LOG_PATH",
    ):
        monkeypatch.delenv(var, raising=False)

    config = PromptLogConfig.load()

    assert config.redact is True
    assert config.posthog_enabled is True


def test_load_respects_env_opt_out_of_redaction(monkeypatch) -> None:
    _no_file_settings(monkeypatch)
    monkeypatch.setenv("OPENSRE_PROMPT_LOG_REDACT", "0")

    config = PromptLogConfig.load()

    assert config.redact is False


def test_load_respects_file_opt_out_of_redaction(monkeypatch) -> None:
    monkeypatch.setattr(
        "config.prompt_log.read_prompt_log_settings",
        lambda: {"redact": False},
    )
    monkeypatch.delenv("OPENSRE_PROMPT_LOG_REDACT", raising=False)

    config = PromptLogConfig.load()

    assert config.redact is False


def test_env_redact_overrides_file_setting(monkeypatch) -> None:
    monkeypatch.setattr(
        "config.prompt_log.read_prompt_log_settings",
        lambda: {"redact": False},
    )
    monkeypatch.setenv("OPENSRE_PROMPT_LOG_REDACT", "1")

    config = PromptLogConfig.load()

    assert config.redact is True


def test_dataclass_default_redact_is_on() -> None:
    """Direct construction (e.g. in other tests/callers) should also default-redact."""
    assert PromptLogConfig().redact is True
