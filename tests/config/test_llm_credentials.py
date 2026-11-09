from __future__ import annotations

import google.auth
from google.auth.exceptions import DefaultCredentialsError

import config.llm_credentials as llm_credentials
from config.llm_auth.credentials import (
    has_llm_api_key,
    llm_api_key_source,
    resolve_for_request,
    status,
)
from config.llm_auth.records import save_provider_auth_record
from config.secrets.store import lookup


def test_status_vertex_ai_configured_when_adc_resolves(monkeypatch) -> None:
    monkeypatch.setenv("VERTEX_AI_PROJECT", "my-gcp-project")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "europe-west1")
    monkeypatch.setattr(google.auth, "default", lambda: (object(), "my-gcp-project"))

    result = status("vertex-ai")

    assert result.configured is True
    assert result.source == "ambient"
    assert "my-gcp-project" in result.detail


def test_status_vertex_ai_not_configured_when_adc_missing_despite_project_env(
    monkeypatch,
) -> None:
    """VERTEX_AI_PROJECT being set is not proof that ADC actually resolves."""
    monkeypatch.setenv("VERTEX_AI_PROJECT", "my-gcp-project")

    def _raise_no_adc() -> tuple[object, str | None]:
        raise DefaultCredentialsError("no ADC found")

    monkeypatch.setattr(google.auth, "default", _raise_no_adc)

    result = status("vertex-ai")

    assert result.configured is False
    assert result.source == "none"


def test_status_vertex_ai_configured_via_metadata_without_project_env(monkeypatch) -> None:
    """ADC discovered through GCE/GKE metadata counts even with no project env set."""
    monkeypatch.delenv("VERTEX_AI_PROJECT", raising=False)
    monkeypatch.delenv("VERTEX_AI_LOCATION", raising=False)
    monkeypatch.setattr(google.auth, "default", lambda: (object(), "discovered-project"))

    result = status("vertex-ai")

    assert result.configured is True
    assert result.source == "ambient"
    assert "discovered-project" in result.detail


def test_status_vertex_ai_not_configured_when_adc_resolves_without_project(
    monkeypatch,
) -> None:
    """ADC succeeding is not enough — a request still needs a resolvable project.

    Regression test: this used to fall back to a display-only "auto-discovered"
    placeholder and report configured=True, even though request routing has no
    project to send and the subsequent LiteLLM call would fail.
    """
    monkeypatch.delenv("VERTEX_AI_PROJECT", raising=False)
    monkeypatch.setattr(google.auth, "default", lambda: (object(), None))

    result = status("vertex-ai")

    assert result.configured is False
    assert result.source == "none"
    assert "VERTEX_AI_PROJECT" in result.detail


def test_status_bedrock_ignores_vertex_project_env(monkeypatch) -> None:
    """The ambient status branch must not cross-check unrelated providers' env vars."""
    monkeypatch.setenv("VERTEX_AI_PROJECT", "my-gcp-project")
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    result = status("bedrock")

    assert result.configured is False
    assert "AWS_REGION" in result.detail


def test_resolve_env_credential_prefers_env_over_local_file(monkeypatch) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "from-env")
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    llm_credentials.save_credential("GITLAB_ACCESS_TOKEN", "from-store")
    assert llm_credentials.resolve_env_credential("GITLAB_ACCESS_TOKEN") == "from-env"


def test_lookup_reports_the_stored_tier(monkeypatch) -> None:
    monkeypatch.setenv("GITLAB_ACCESS_TOKEN", "from-env")
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    llm_credentials.save_credential("GITLAB_ACCESS_TOKEN", "from-store")
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)
    found = lookup("GITLAB_ACCESS_TOKEN")

    assert found.value == "from-store"
    assert found.tier == "fallback"


def test_unmanaged_llm_api_key_source_reports_env_stored_and_none(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("EXPERIMENTAL_API_KEY", raising=False)

    assert llm_api_key_source("EXPERIMENTAL_API_KEY") == "none"
    llm_credentials.save_credential("EXPERIMENTAL_API_KEY", "from-store")
    assert llm_api_key_source("EXPERIMENTAL_API_KEY") == "fallback"
    monkeypatch.setenv("EXPERIMENTAL_API_KEY", "from-env")
    assert llm_api_key_source("EXPERIMENTAL_API_KEY") == "env"


def test_managed_llm_api_key_source_uses_metadata_without_reading_secret(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    save_provider_auth_record(
        provider="openai",
        auth_name="openai",
        kind="api_key",
        source="fallback",
        detail="OPENAI_API_KEY stored in the local credentials file.",
        env_var="OPENAI_API_KEY",
    )

    assert llm_api_key_source("OPENAI_API_KEY") == "metadata"
    assert has_llm_api_key("OPENAI_API_KEY") is True


def test_managed_missing_metadata_reports_none(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))

    assert llm_api_key_source("OPENAI_API_KEY") == "none"
    assert has_llm_api_key("OPENAI_API_KEY") is False


def test_request_resolution_marks_deleted_credential_metadata_stale(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    save_provider_auth_record(
        provider="deepseek",
        auth_name="deepseek",
        kind="api_key",
        source="fallback",
        detail="DEEPSEEK_API_KEY stored in the local credentials file.",
        env_var="DEEPSEEK_API_KEY",
    )

    before = status("deepseek")
    resolution = resolve_for_request("deepseek")
    after = status("deepseek")

    assert before.configured is True
    assert before.stale is False
    assert resolution.ok is False
    assert after.configured is True
    assert after.stale is True
    assert after.verified is False
    assert "Missing credential" in after.detail


def test_legacy_keyring_metadata_normalizes_to_fallback(monkeypatch, tmp_path) -> None:
    """Records written before the OS keychain was removed still resolve."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
    save_provider_auth_record(
        provider="openai",
        auth_name="openai",
        kind="api_key",
        source="keyring",
        detail="OPENAI_API_KEY stored in the system keychain.",
        env_var="OPENAI_API_KEY",
    )

    result = status("openai")

    assert result.configured is True
    assert result.source == "metadata"


def test_llm_credential_record_round_trips_in_local_file(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    llm_credentials.save_llm_credential_record(
        "provider-auth:deepseek",
        {"provider": "deepseek", "source": "fallback", "empty": ""},
    )

    assert llm_credentials.resolve_llm_credential_record("provider-auth:deepseek") == {
        "provider": "deepseek",
        "source": "fallback",
    }

    llm_credentials.delete_llm_credential_record("provider-auth:deepseek")
    assert llm_credentials.resolve_llm_credential_record("provider-auth:deepseek") == {}


def test_get_keyring_setup_instructions_when_file_refuses(monkeypatch) -> None:
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)

    lines = llm_credentials.get_keyring_setup_instructions("ANTHROPIC_API_KEY")

    assert any("could not write" in line for line in lines)
    assert any("write access to that path" in line for line in lines)
    assert any("export ANTHROPIC_API_KEY" in line for line in lines)


def test_get_keyring_setup_instructions_when_storage_is_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")

    lines = llm_credentials.get_keyring_setup_instructions("OPENAI_API_KEY")

    assert lines == (
        "Local credential storage is disabled by OPENSRE_DISABLE_KEYRING.",
        "Unset OPENSRE_DISABLE_KEYRING and rerun `opensre onboard` to save "
        "OPENAI_API_KEY, or export OPENAI_API_KEY in your shell.",
    )
