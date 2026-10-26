from __future__ import annotations

import os
import stat

import pytest

from config.env_file import is_sensitive_env_key, sync_env_secret, sync_env_values
from config.llm_credentials import resolve_env_credential
from config.setup_store import load_local_config
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE
from surfaces.shared.llm_setup.env_sync import (
    sync_provider_env,
    sync_reasoning_model_env,
)

_SKIP_AS_ROOT = not hasattr(os, "getuid") or os.getuid() == 0


@pytest.fixture(autouse=True)
def _redirect_wizard_store(tmp_path, monkeypatch) -> None:
    """Keep sync_provider_env store updates off the developer's ~/.opensre."""
    monkeypatch.setattr(
        "config.setup_store.get_store_path",
        lambda: tmp_path / "opensre.json",
    )


@pytest.mark.parametrize(
    "key",
    [
        # Suffix-style sensitive keys (existing behavior).
        "GITLAB_ACCESS_TOKEN",
        "ANTHROPIC_API_KEY",
        "AWS_SECRET_ACCESS_KEY",
        "DB_PASSWORD",
        # Bare sensitive keys (previously slipped past the suffix-only filter
        # and reached plain-text .env, which CodeQL flagged as alert #1019).
        "PASSWORD",
        "TOKEN",
        "SECRET",
        "KEY",
        "APIKEY",
        "CREDENTIAL",
        # Substring-based sensitives.
        "DATABASE_CONNECTION_STRING",
        # Inline kubeconfig YAML embeds credentials; path-only KUBECONFIG does not.
        "KUBECONFIG_CONTENT",
    ],
)
def test_is_sensitive_env_key_marks_secrets(key: str) -> None:
    assert is_sensitive_env_key(key) is True


@pytest.mark.parametrize(
    "key",
    [
        # Non-secret configuration env vars must stay writable to .env.
        "LLM_PROVIDER",
        "OPENAI_REASONING_MODEL",
        "OPENAI_MODEL",
        "GITLAB_BASE_URL",
        "ENV",
        # Terminal token is not in the sensitive set even though "TOKEN"
        # appears as a substring — the limit count is not itself a secret.
        "OPENAI_TOKEN_LIMIT",
        # Explicit exception: a public discord key is not sensitive.
        "DISCORD_PUBLIC_KEY",
        # Explicit exception: paired with a private key, not a secret itself.
        "MONGODB_ATLAS_PUBLIC_KEY",
        # Path to a kubeconfig file is not the credential itself.
        "KUBECONFIG",
        "HELM_KUBECONFIG",
    ],
)
def test_is_sensitive_env_key_leaves_non_secrets(key: str) -> None:
    assert is_sensitive_env_key(key) is False


def test_sync_provider_env_updates_provider_specific_keys(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENV=development\n"
        "LLM_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=legacy-anthropic\n"
        "OPENAI_API_KEY=old-key\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "ENV=development\n" in content
    assert content.count("LLM_PROVIDER=") == 1
    assert "LLM_PROVIDER=openai\n" in content
    assert "OPENAI_API_KEY=old-key\n" in content
    assert "ANTHROPIC_API_KEY=legacy-anthropic\n" in content
    assert "OPENAI_REASONING_MODEL=gpt-5-mini\n" in content
    assert "OPENAI_MODEL=gpt-5-mini\n" in content


def test_sync_provider_env_preserves_host_credential(tmp_path, monkeypatch) -> None:
    """#3291: the wizard writes the Ollama host to .env, then calls sync_provider_env
    in the same run. A host credential is non-secret runtime config, so it must
    survive that sync — otherwise OLLAMA_HOST is stripped from both .env and the
    environment and the custom host is silently lost again."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://10.0.0.5:11434")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "OLLAMA_HOST=http://10.0.0.5:11434\nLLM_PROVIDER=ollama\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["ollama"],
        model="llama3.1",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "OLLAMA_HOST=http://10.0.0.5:11434\n" in content  # kept in .env
    assert os.environ["OLLAMA_HOST"] == "http://10.0.0.5:11434"  # kept in runtime env
    assert "LLM_PROVIDER=ollama\n" in content
    assert "OLLAMA_MODEL=llama3.1\n" in content


def test_sync_provider_env_preserves_integration_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=manual-fallback-token\nLLM_PROVIDER=anthropic\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=manual-fallback-token\n" in content
    assert "LLM_PROVIDER=openai\n" in content


def test_sync_provider_env_updates_wizard_store(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5-mini",
        env_path=env_path,
    )

    stored = load_local_config(store_path)
    local = stored["targets"]["local"]
    assert local["provider"] == "openai"
    assert local["model"] == "gpt-5-mini"
    assert local["api_key_env"] == "OPENAI_API_KEY"
    assert local["model_env"] == "OPENAI_REASONING_MODEL"


def test_sync_reasoning_model_env_updates_wizard_store(tmp_path, monkeypatch) -> None:
    store_path = tmp_path / "opensre.json"
    env_path = tmp_path / ".env"
    monkeypatch.setenv("LLM_PROVIDER", "openai")

    sync_reasoning_model_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    stored = load_local_config(store_path)
    local = stored["targets"]["local"]
    assert local["provider"] == "openai"
    assert local["model"] == "gpt-5.4-mini"
    assert env_path.read_text(encoding="utf-8") == (
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\nOPENAI_MODEL=gpt-5.4-mini\n"
    )


def test_sync_provider_env_appends_to_file_without_final_newline(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "ENV=development\n"
        "LLM_PROVIDER=anthropic\n"
        "ANTHROPIC_API_KEY=legacy-anthropic\n"
        "TEST_ENV=no-new-line",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert content.endswith("OPENAI_MODEL=gpt-5-mini\n")
    assert "LLM_PROVIDER=openai\n" in content
    assert "ANTHROPIC_API_KEY=legacy-anthropic\n" in content


def test_sync_provider_env_codex_writes_codex_model(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    sync_provider_env(
        provider=PROVIDER_BY_VALUE["codex"],
        model="",
        env_path=env_path,
    )
    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=codex\n" in content
    assert "CODEX_MODEL=\n" in content


def test_sync_provider_env_scrubs_legacy_auth_method(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_AUTH_METHOD", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\nLLM_AUTH_METHOD=oauth\nOPENAI_REASONING_MODEL=gpt-5.5\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.5",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=openai\n" in content
    assert "LLM_AUTH_METHOD=" not in content
    assert "LLM_AUTH_METHOD" not in os.environ


def test_sync_provider_env_gemini_cli_writes_model(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_CLI_MODEL", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    sync_provider_env(
        provider=PROVIDER_BY_VALUE["gemini-cli"],
        model="",
        env_path=env_path,
    )
    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=gemini-cli\n" in content
    assert "GEMINI_CLI_MODEL=\n" in content


def test_sync_provider_env_removes_stale_toolcall_and_classification_keys(
    tmp_path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n"
        "OPENAI_CLASSIFICATION_MODEL=gpt-5.4-mini\n"
        "CODEX_MODEL=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["codex"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=codex\n" in content
    assert "CODEX_MODEL=gpt-5.4-mini\n" in content
    assert "OPENAI_TOOLCALL_MODEL=" not in content
    assert "OPENAI_CLASSIFICATION_MODEL=" not in content
    assert "OPENAI_REASONING_MODEL=" not in content
    assert "OPENAI_TOOLCALL_MODEL" not in os.environ


def test_sync_provider_env_loads_preserved_keys_from_env_file(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n"
        "OPENAI_CLASSIFICATION_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("OPENAI_TOOLCALL_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_CLASSIFICATION_MODEL", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "stale")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-5.4-mini"
    assert os.environ["OPENAI_TOOLCALL_MODEL"] == "gpt-5.4-mini"
    assert os.environ["OPENAI_CLASSIFICATION_MODEL"] == "gpt-5.4-mini"


def test_sync_provider_env_preserves_active_provider_toolcall_key(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n" in content
    assert os.environ.get("OPENAI_TOOLCALL_MODEL") == "gpt-5.4-mini"


def test_sync_provider_env_updates_os_environ(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\nOPENAI_REASONING_MODEL=gpt-5.4-mini\nOPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["codex"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    assert os.environ["LLM_PROVIDER"] == "codex"
    assert os.environ["CODEX_MODEL"] == "gpt-5.4-mini"
    assert "OPENAI_TOOLCALL_MODEL" not in os.environ
    assert "OPENAI_REASONING_MODEL" not in os.environ


def test_sync_provider_env_skips_empty_preserved_values_in_os_environ(
    tmp_path, monkeypatch
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.delenv("OPENAI_TOOLCALL_MODEL", raising=False)

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-5.4-mini"
    assert "OPENAI_TOOLCALL_MODEL" not in os.environ


def test_sync_provider_env_skips_empty_toolcall_model_override(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "gpt-5.4-mini")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        toolcall_model="",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "OPENAI_TOOLCALL_MODEL=gpt-5.4-mini\n" in content
    assert os.environ["OPENAI_TOOLCALL_MODEL"] == "gpt-5.4-mini"


def test_sync_provider_env_writes_toolcall_model_atomically(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=openai\n"
        "OPENAI_REASONING_MODEL=gpt-5.4-mini\n"
        "OPENAI_MODEL=gpt-5.4-mini\n"
        "OPENAI_TOOLCALL_MODEL=old-toolcall\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("OPENAI_TOOLCALL_MODEL", "old-toolcall")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        toolcall_model="gpt-5.4-nano",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "OPENAI_REASONING_MODEL=gpt-5.4-mini\n" in content
    assert "OPENAI_TOOLCALL_MODEL=gpt-5.4-nano\n" in content
    assert os.environ["OPENAI_REASONING_MODEL"] == "gpt-5.4-mini"
    assert os.environ["OPENAI_TOOLCALL_MODEL"] == "gpt-5.4-nano"


def test_sync_provider_env_sets_and_clears_azure_litellm_transport(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    azure = PROVIDER_BY_VALUE["azure-openai"]
    env_path.write_text(
        "LLM_PROVIDER=anthropic\n"
        "OPENSRE_LLM_TRANSPORT=litellm\n"
        "AZURE_OPENAI_BASE_URL=https://example.openai.azure.com\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["openai"],
        model="gpt-5.4-mini",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=openai\n" in content
    assert "OPENSRE_LLM_TRANSPORT=" not in content

    sync_provider_env(
        provider=azure,
        model="gpt-5.4-mini",
        extra_env={
            "AZURE_OPENAI_BASE_URL": "https://example.openai.azure.com",
            "AZURE_OPENAI_API_VERSION": "2024-10-21",
        },
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=azure-openai\n" in content
    assert "OPENSRE_LLM_TRANSPORT=litellm\n" in content
    assert "AZURE_OPENAI_API_VERSION=2024-10-21\n" in content
    assert os.environ["OPENSRE_LLM_TRANSPORT"] == "litellm"


def test_sync_provider_env_preserves_azure_endpoint_on_model_switch(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    azure = PROVIDER_BY_VALUE["azure-openai"]
    env_path.write_text(
        "LLM_PROVIDER=azure-openai\n"
        "OPENSRE_LLM_TRANSPORT=litellm\n"
        "AZURE_OPENAI_BASE_URL=https://example.openai.azure.com\n"
        "AZURE_OPENAI_API_VERSION=2024-10-21\n"
        "AZURE_OPENAI_REASONING_MODEL=gpt-5.4-mini\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=azure,
        model="gpt-5.4",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "AZURE_OPENAI_BASE_URL=https://example.openai.azure.com\n" in content
    assert "AZURE_OPENAI_API_VERSION=2024-10-21\n" in content
    assert "AZURE_OPENAI_REASONING_MODEL=gpt-5.4\n" in content
    assert os.environ["AZURE_OPENAI_BASE_URL"] == "https://example.openai.azure.com"


@pytest.mark.skipif(_SKIP_AS_ROOT, reason="root bypasses file permission checks")
def test_sync_provider_env_permission_error(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    env_path.chmod(stat.S_IRUSR)  # read-only
    try:
        with pytest.raises(PermissionError, match="permission denied"):
            sync_provider_env(
                provider=PROVIDER_BY_VALUE["openai"],
                model="gpt-4o",
                env_path=env_path,
            )
    finally:
        env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(os.name == "nt", reason="owner-only mode is a POSIX .env write")
def test_sync_env_values_creates_env_file_with_owner_only_mode(tmp_path) -> None:
    env_path = tmp_path / ".env"

    sync_env_values({"DD_SITE": "datadoghq.com"}, env_path=env_path)

    mode = stat.S_IMODE(env_path.stat().st_mode)
    assert mode == 0o600


def test_sync_env_values_writes_sensitive_keys(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\n", encoding="utf-8")

    sync_env_values({"GITLAB_ACCESS_TOKEN": "secret"}, env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "FOO=bar\n" in content
    assert "GITLAB_ACCESS_TOKEN=secret\n" in content


def test_set_env_value_writes_sensitive_keys() -> None:
    from config.env_file import set_env_value

    lines = set_env_value(["FOO=bar\n"], "GITLAB_ACCESS_TOKEN", "secret")

    assert lines == ["FOO=bar\n", "GITLAB_ACCESS_TOKEN=secret\n"]


def test_set_env_value_rejects_newline_injection() -> None:
    from config.env_file import set_env_value

    with pytest.raises(ValueError, match="single line"):
        set_env_value(["FOO=bar\n"], "FOO", "ok\nTELEGRAM_BOT_TOKEN=leaked")


def test_set_env_value_replaces_export_prefixed_assignment() -> None:
    from config.env_file import set_env_value

    lines = set_env_value(["export DD_SITE=old\n"], "DD_SITE", "datadoghq.eu")

    assert lines == ["DD_SITE=datadoghq.eu\n"]


def test_sync_env_secret_raises_when_storage_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "config.env_file.save_credential",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Local credential storage is unavailable on this machine.")
        ),
    )

    with pytest.raises(RuntimeError, match="Failed to persist.*credentials.json"):
        sync_env_secret(
            "GITLAB_ACCESS_TOKEN",
            "gl-secret-token",
            env_path=tmp_path / ".env",
        )


def test_sync_env_values_preserves_existing_secrets_and_updates_public_keys(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("GITLAB_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", tmp_path / "unused.env")

    env_path = tmp_path / ".env"
    env_path.write_text(
        "GITLAB_BASE_URL=https://gitlab.example.com\nGITLAB_ACCESS_TOKEN=legacy-plaintext\n",
        encoding="utf-8",
    )

    sync_env_secret("GITLAB_ACCESS_TOKEN", "gl-secret-token", env_path=env_path)
    sync_env_values(
        {"GITLAB_BASE_URL": "https://gitlab.corp.com"},
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "GITLAB_BASE_URL=https://gitlab.corp.com\n" in content
    assert "GITLAB_ACCESS_TOKEN=gl-secret-token\n" in content
    assert resolve_env_credential("GITLAB_ACCESS_TOKEN") == "gl-secret-token"


def test_sync_env_secret_with_blank_value_deletes_the_stored_secret(tmp_path, monkeypatch) -> None:
    """A field cleared back to blank must remove the old stored entry, not just skip writing.

    apply_setup calls sync_env_secret for every secret field on every save,
    submitted or not (see integrations/setup_flow.py:_persist_env) — so clearing
    an optional token in the UI has to reach here as a real deletion.
    """
    monkeypatch.delenv("DAGSTER_API_TOKEN", raising=False)
    monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
    env_path = tmp_path / ".env"

    sync_env_secret("DAGSTER_API_TOKEN", "dag_stale_token", env_path=env_path)
    assert resolve_env_credential("DAGSTER_API_TOKEN") == "dag_stale_token"
    assert "DAGSTER_API_TOKEN=dag_stale_token\n" in env_path.read_text(encoding="utf-8")

    sync_env_secret("DAGSTER_API_TOKEN", "", env_path=env_path)

    assert resolve_env_credential("DAGSTER_API_TOKEN") == ""
    assert "DAGSTER_API_TOKEN=" not in env_path.read_text(encoding="utf-8")


@pytest.mark.skipif(_SKIP_AS_ROOT, reason="root bypasses file permission checks")
def test_sync_env_values_permission_error(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FOO=bar\n", encoding="utf-8")
    env_path.chmod(stat.S_IRUSR)
    try:
        with pytest.raises(PermissionError, match="permission denied"):
            sync_env_values({"FOO": "baz"}, env_path=env_path)
    finally:
        env_path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_sync_env_values_preserves_telegram_bot_token(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "TELEGRAM_BOT_TOKEN=manual-fallback-token\nDD_SITE=old\n",
        encoding="utf-8",
    )

    sync_env_values({"DD_SITE": "datadoghq.eu"}, env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN=manual-fallback-token\n" in content
    assert "DD_SITE=datadoghq.eu" in content


def test_sync_env_values_preserves_multiple_existing_secrets(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DD_API_KEY=datadog-api\nDD_APP_KEY=datadog-app\n",
        encoding="utf-8",
    )

    sync_env_values({"DD_SITE": "datadoghq.com"}, env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "DD_API_KEY=datadog-api\n" in content
    assert "DD_APP_KEY=datadog-app\n" in content
    assert "DD_SITE=datadoghq.com" in content


def test_sync_env_values_empty_update_preserves_existing_secrets(tmp_path) -> None:
    """Wizard paths call ``sync_env_values({})`` after integration setup."""
    env_path = tmp_path / ".env"
    env_path.write_text("TELEGRAM_BOT_TOKEN=manual-fallback-token\n", encoding="utf-8")

    sync_env_values({}, env_path=env_path)

    assert env_path.read_text(encoding="utf-8") == "TELEGRAM_BOT_TOKEN=manual-fallback-token\n"


def test_sync_env_values_preserves_export_prefixed_secrets(tmp_path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "export TELEGRAM_BOT_TOKEN=manual-fallback-token\nDD_SITE=old\n",
        encoding="utf-8",
    )

    sync_env_values({"DD_SITE": "datadoghq.eu"}, env_path=env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "export TELEGRAM_BOT_TOKEN=manual-fallback-token\n" in content
    assert "DD_SITE=datadoghq.eu" in content


def test_sync_provider_env_preserves_custom_openai_base_url(tmp_path, monkeypatch) -> None:
    # The custom gateway base URL must survive the sync (not be stripped), and
    # custom providers must NOT get azure's OPENSRE_LLM_TRANSPORT=litellm flag.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1")
    env_path = tmp_path / ".env"
    env_path.write_text("ENV=development\n", encoding="utf-8")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["custom-openai"],
        model="gpt-5.4",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=custom-openai\n" in content
    assert "CUSTOM_OPENAI_BASE_URL=http://localhost:4000/v1\n" in content
    assert "CUSTOM_OPENAI_REASONING_MODEL=gpt-5.4\n" in content
    assert "OPENSRE_LLM_TRANSPORT" not in content


def test_sync_provider_env_preserves_custom_openai_model_when_blank(tmp_path, monkeypatch) -> None:
    # Switching/restoring a custom provider with no explicit model must NOT blank a
    # previously-working CUSTOM_OPENAI_REASONING_MODEL (which would fail the next
    # config load) — the configured model is preserved from the .env.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CUSTOM_OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("CUSTOM_OPENAI_MODEL", raising=False)
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=custom-openai\n"
        "CUSTOM_OPENAI_BASE_URL=http://localhost:4000/v1\n"
        "CUSTOM_OPENAI_REASONING_MODEL=gpt-5.4\n"
        "CUSTOM_OPENAI_MODEL=gpt-5.4\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["custom-openai"],
        model="",  # provider default is "" for custom gateways
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "CUSTOM_OPENAI_REASONING_MODEL=gpt-5.4\n" in content
    assert "CUSTOM_OPENAI_MODEL=gpt-5.4\n" in content
    assert "CUSTOM_OPENAI_REASONING_MODEL=\n" not in content
    assert "CUSTOM_OPENAI_BASE_URL=http://localhost:4000/v1\n" in content


def test_sync_provider_env_preserves_custom_model_from_environ_when_blank(
    tmp_path, monkeypatch
) -> None:
    # The configured model may live only in the process env (not yet in .env);
    # a blank switch must still preserve it rather than write an empty value.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CUSTOM_ANTHROPIC_MODEL", raising=False)
    monkeypatch.setenv("CUSTOM_ANTHROPIC_BASE_URL", "https://proxy.example.com")
    monkeypatch.setenv("CUSTOM_ANTHROPIC_REASONING_MODEL", "claude-opus-4-7")
    env_path = tmp_path / ".env"
    env_path.write_text("ENV=development\n", encoding="utf-8")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["custom-anthropic"],
        model="",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "CUSTOM_ANTHROPIC_REASONING_MODEL=claude-opus-4-7\n" in content
    assert "CUSTOM_ANTHROPIC_REASONING_MODEL=\n" not in content


def test_sync_provider_env_preserves_custom_legacy_model_when_blank(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CUSTOM_OPENAI_REASONING_MODEL", raising=False)
    monkeypatch.delenv("CUSTOM_OPENAI_MODEL", raising=False)
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1")
    env_path = tmp_path / ".env"
    env_path.write_text(
        "LLM_PROVIDER=custom-openai\n"
        "CUSTOM_OPENAI_BASE_URL=http://localhost:4000/v1\n"
        "CUSTOM_OPENAI_MODEL=gateway-model\n",
        encoding="utf-8",
    )

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["custom-openai"],
        model="",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "CUSTOM_OPENAI_REASONING_MODEL=gateway-model\n" in content
    assert "CUSTOM_OPENAI_MODEL=gateway-model\n" in content


def test_sync_provider_env_cli_provider_keeps_empty_model(tmp_path, monkeypatch) -> None:
    # The blank-model preservation is scoped to custom gateways; a CLI provider
    # with an empty model still writes its (empty) CLI-default slot as before.
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("ENV=development\n", encoding="utf-8")

    sync_provider_env(
        provider=PROVIDER_BY_VALUE["codex"],
        model="",
        env_path=env_path,
    )

    content = env_path.read_text(encoding="utf-8")
    assert "LLM_PROVIDER=codex\n" in content
