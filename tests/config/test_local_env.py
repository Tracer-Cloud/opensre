from __future__ import annotations

import json
from pathlib import Path

from config import local_env


def _write_store(path: Path, *, provider: str = "openai", model: str = "gpt-5.5") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "targets": {
                    "local": {
                        "provider": provider,
                        "model": model,
                        "model_env": "CODEX_MODEL",
                    }
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_frozen_bootstrap_uses_installed_env_and_wizard_store(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".opensre" / ".env"
    store_path = tmp_path / ".opensre" / "opensre.json"
    _write_store(store_path)

    monkeypatch.setattr(local_env.sys, "frozen", True, raising=False)
    monkeypatch.setattr(local_env, "INSTALLED_ENV_PATH", env_path)
    monkeypatch.setattr(local_env, "WIZARD_STORE_PATH", store_path)
    monkeypatch.delenv(local_env.OPENSRE_PROJECT_ENV_PATH_ENV, raising=False)
    for key in ("LLM_PROVIDER", "LLM_AUTH_METHOD", "CODEX_MODEL"):
        monkeypatch.delenv(key, raising=False)

    loaded = local_env.bootstrap_opensre_env()

    assert loaded == env_path
    assert local_env.get_project_env_path() == env_path
    assert local_env.os.environ["LLM_PROVIDER"] == "openai"
    assert "LLM_AUTH_METHOD" not in local_env.os.environ
    assert local_env.os.environ["CODEX_MODEL"] == "gpt-5.5"


def test_explicit_env_provider_blocks_mismatched_wizard_store_defaults(
    tmp_path: Path, monkeypatch
) -> None:
    env_path = tmp_path / ".opensre" / ".env"
    store_path = tmp_path / "opensre.json"
    _write_store(store_path)

    monkeypatch.setattr(local_env.sys, "frozen", True, raising=False)
    monkeypatch.setattr(local_env, "INSTALLED_ENV_PATH", env_path)
    monkeypatch.setattr(local_env, "WIZARD_STORE_PATH", store_path)
    monkeypatch.delenv(local_env.OPENSRE_PROJECT_ENV_PATH_ENV, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_AUTH_METHOD", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)

    local_env.bootstrap_opensre_env()

    assert local_env.os.environ["LLM_PROVIDER"] == "anthropic"
    assert "LLM_AUTH_METHOD" not in local_env.os.environ
    assert "CODEX_MODEL" not in local_env.os.environ


def test_project_env_path_override_does_not_apply_wizard_store_defaults(
    tmp_path: Path, monkeypatch
) -> None:
    env_path = tmp_path / "project.env"
    store_path = tmp_path / ".opensre" / "opensre.json"
    _write_store(store_path)

    monkeypatch.setattr(local_env.sys, "frozen", True, raising=False)
    monkeypatch.setattr(local_env, "WIZARD_STORE_PATH", store_path)
    monkeypatch.setenv(local_env.OPENSRE_PROJECT_ENV_PATH_ENV, str(env_path))
    for key in ("LLM_PROVIDER", "LLM_AUTH_METHOD", "CODEX_MODEL"):
        monkeypatch.delenv(key, raising=False)

    loaded = local_env.bootstrap_opensre_env()

    assert loaded == env_path
    assert "LLM_PROVIDER" not in local_env.os.environ
    assert "LLM_AUTH_METHOD" not in local_env.os.environ
    assert "CODEX_MODEL" not in local_env.os.environ


def test_project_env_path_override_wins_over_installed_default(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / "project.env"
    installed_path = tmp_path / ".opensre" / ".env"
    env_path.write_text("LLM_PROVIDER=openai\nLLM_AUTH_METHOD=oauth\nCODEX_MODEL=gpt-5.4\n")

    monkeypatch.setattr(local_env.sys, "frozen", True, raising=False)
    monkeypatch.setattr(local_env, "INSTALLED_ENV_PATH", installed_path)
    monkeypatch.setenv(local_env.OPENSRE_PROJECT_ENV_PATH_ENV, str(env_path))
    for key in ("LLM_PROVIDER", "LLM_AUTH_METHOD", "CODEX_MODEL"):
        monkeypatch.delenv(key, raising=False)

    loaded = local_env.bootstrap_opensre_env()

    assert loaded == env_path
    assert local_env.get_project_env_path() == env_path
    assert local_env.os.environ["LLM_PROVIDER"] == "openai"
    assert local_env.os.environ["CODEX_MODEL"] == "gpt-5.4"


def test_skip_env_file_disables_env_and_store_bootstrap(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".opensre" / ".env"
    store_path = tmp_path / ".opensre" / "opensre.json"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    _write_store(store_path)

    monkeypatch.setattr(local_env.sys, "frozen", True, raising=False)
    monkeypatch.setattr(local_env, "INSTALLED_ENV_PATH", env_path)
    monkeypatch.setattr(local_env, "WIZARD_STORE_PATH", store_path)
    monkeypatch.setenv("GRAFANA_CONFIG_SKIP_ENV_FILE", "1")
    monkeypatch.delenv(local_env.OPENSRE_PROJECT_ENV_PATH_ENV, raising=False)
    for key in ("LLM_PROVIDER", "LLM_AUTH_METHOD", "CODEX_MODEL"):
        monkeypatch.delenv(key, raising=False)

    loaded = local_env.bootstrap_opensre_env()

    assert loaded == env_path
    assert "LLM_PROVIDER" not in local_env.os.environ
    assert "LLM_AUTH_METHOD" not in local_env.os.environ
    assert "CODEX_MODEL" not in local_env.os.environ


def test_blank_env_provider_blocks_env_and_store_defaults(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".opensre" / ".env"
    store_path = tmp_path / ".opensre" / "opensre.json"
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("LLM_PROVIDER=anthropic\n", encoding="utf-8")
    _write_store(store_path)

    monkeypatch.setattr(local_env.sys, "frozen", True, raising=False)
    monkeypatch.setattr(local_env, "INSTALLED_ENV_PATH", env_path)
    monkeypatch.setattr(local_env, "WIZARD_STORE_PATH", store_path)
    monkeypatch.delenv(local_env.OPENSRE_PROJECT_ENV_PATH_ENV, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.delenv("LLM_AUTH_METHOD", raising=False)
    monkeypatch.delenv("CODEX_MODEL", raising=False)

    local_env.bootstrap_opensre_env()

    assert local_env.os.environ["LLM_PROVIDER"] == ""
    assert "LLM_AUTH_METHOD" not in local_env.os.environ
    assert "CODEX_MODEL" not in local_env.os.environ


def test_bootstrap_loads_export_prefixed_assignments(tmp_path: Path, monkeypatch) -> None:
    """Shell-sourced env files use ``export KEY=value``; that must populate KEY."""
    env_path = tmp_path / "project.env"
    env_path.write_text("export LLM_PROVIDER=anthropic\n", encoding="utf-8")

    monkeypatch.setenv(local_env.OPENSRE_PROJECT_ENV_PATH_ENV, str(env_path))
    monkeypatch.delenv("GRAFANA_CONFIG_SKIP_ENV_FILE", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    local_env.bootstrap_opensre_env()

    assert local_env.os.environ["LLM_PROVIDER"] == "anthropic"
