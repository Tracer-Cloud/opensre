"""End-to-end persistence scenarios for /model, onboard paths, and wizard env sync."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from rich.console import Console

import config.setup_store as wizard_store
import surfaces.shared.llm_setup.env_sync as env_sync
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.command_registry import repl_data as repl_data_module
from surfaces.interactive_shell.session import Session
from surfaces.shared.llm_setup.catalog import PROJECT_ENV_PATH, PROJECT_ROOT, PROVIDER_BY_VALUE


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


@pytest.fixture
def persistence_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Isolate .env and opensre.json writes for scenario tests."""
    env_path = tmp_path / "project.env"
    store_path = tmp_path / "opensre.json"
    monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
    monkeypatch.setattr(wizard_store, "get_store_path", lambda: store_path)
    return {"env": env_path, "store": store_path}


@pytest.fixture
def patch_llm_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Fake:
        provider = "anthropic"
        anthropic_reasoning_model = "claude-opus-4-7"
        anthropic_toolcall_model = "claude-haiku-4-5-20251001"

    monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _Fake())


class TestProjectPaths:
    def test_default_env_path_is_repo_root_dotenv(self) -> None:
        assert (PROJECT_ROOT / "pyproject.toml").is_file()
        assert PROJECT_ENV_PATH == PROJECT_ROOT / ".env"


class TestEnvSyncPersistence:
    def test_provider_switch_writes_env_and_store(
        self, persistence_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        env_sync.sync_provider_env(
            provider=PROVIDER_BY_VALUE["openai"],
            model="gpt-5-mini",
            env_path=persistence_paths["env"],
        )
        env_body = persistence_paths["env"].read_text(encoding="utf-8")
        assert "LLM_PROVIDER=openai" in env_body
        assert "OPENAI_REASONING_MODEL=gpt-5-mini" in env_body

        stored = wizard_store.load_local_config(persistence_paths["store"])
        local = stored["targets"]["local"]
        assert local["provider"] == "openai"
        assert local["model"] == "gpt-5-mini"
        assert local["api_key_env"] == "OPENAI_API_KEY"

    def test_reasoning_only_updates_store_model(
        self, persistence_paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wizard_store.save_local_config(
            wizard_mode="quickstart",
            provider="openai",
            model="gpt-5-mini",
            api_key_env="OPENAI_API_KEY",
            model_env="OPENAI_REASONING_MODEL",
            probes={},
            path=persistence_paths["store"],
        )
        monkeypatch.setenv("LLM_PROVIDER", "openai")

        env_sync.sync_reasoning_model_env(
            provider=PROVIDER_BY_VALUE["openai"],
            model="gpt-5.4-mini",
            env_path=persistence_paths["env"],
        )

        stored = wizard_store.load_local_config(persistence_paths["store"])
        assert stored["targets"]["local"]["provider"] == "openai"
        assert stored["targets"]["local"]["model"] == "gpt-5.4-mini"
        assert "LLM_PROVIDER=" not in persistence_paths["env"].read_text(encoding="utf-8")

    def test_cli_provider_switch_clears_stale_api_key_env(self, tmp_path: Path) -> None:
        store_path = tmp_path / "opensre.json"
        wizard_store.save_local_config(
            wizard_mode="quickstart",
            provider="anthropic",
            model="claude-haiku",
            api_key_env="ANTHROPIC_API_KEY",
            model_env="ANTHROPIC_REASONING_MODEL",
            probes={},
            path=store_path,
        )
        wizard_store.update_local_llm_selection(
            provider="claude-code",
            model="",
            api_key_env="",
            model_env="CLAUDE_CODE_MODEL",
            path=store_path,
        )
        local = wizard_store.load_local_config(store_path)["targets"]["local"]
        assert local["provider"] == "claude-code"
        assert local["api_key_env"] == ""


class TestReplModelPersistence:
    pytestmark = pytest.mark.usefixtures("patch_llm_settings")

    def test_model_set_provider_updates_env_and_store(
        self,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        ok = dispatch_slash("/model set anthropic claude-opus-4-7", Session(), console)
        assert ok is True
        assert "switched LLM provider" in buf.getvalue()

        env_body = persistence_paths["env"].read_text(encoding="utf-8")
        assert "LLM_PROVIDER=anthropic" in env_body
        assert "ANTHROPIC_REASONING_MODEL=claude-opus-4-7" in env_body

        stored = wizard_store.load_local_config(persistence_paths["store"])["targets"]["local"]
        assert stored["provider"] == "anthropic"
        assert stored["model"] == "claude-opus-4-7"

    def test_model_set_bare_model_updates_env_and_store(
        self,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: None)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        wizard_store.save_local_config(
            wizard_mode="quickstart",
            provider="anthropic",
            model="claude-haiku",
            api_key_env="ANTHROPIC_API_KEY",
            model_env="ANTHROPIC_REASONING_MODEL",
            probes={},
            path=persistence_paths["store"],
        )

        console, _ = _capture()
        dispatch_slash("/model set claude-opus-4-7", Session(), console)

        env_body = persistence_paths["env"].read_text(encoding="utf-8")
        assert "ANTHROPIC_REASONING_MODEL=claude-opus-4-7" in env_body
        assert "LLM_PROVIDER=" not in env_body

        stored = wizard_store.load_local_config(persistence_paths["store"])["targets"]["local"]
        assert stored["provider"] == "anthropic"
        assert stored["model"] == "claude-opus-4-7"

    def test_model_set_with_toolcall_flag_persists_both_slots(
        self,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        ok = dispatch_slash(
            "/model set anthropic claude-opus-4-7 --toolcall-model claude-haiku-4-5",
            Session(),
            console,
        )
        assert ok is True
        assert "toolcall model:" in buf.getvalue()

        env_body = persistence_paths["env"].read_text(encoding="utf-8")
        assert "ANTHROPIC_REASONING_MODEL=claude-opus-4-7" in env_body
        assert "ANTHROPIC_TOOLCALL_MODEL=claude-haiku-4-5" in env_body

        stored = wizard_store.load_local_config(persistence_paths["store"])["targets"]["local"]
        assert stored["model"] == "claude-opus-4-7"

    def test_model_toolcall_set_does_not_rewrite_store_reasoning_model(
        self,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: None)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        wizard_store.save_local_config(
            wizard_mode="quickstart",
            provider="anthropic",
            model="claude-opus-4-7",
            api_key_env="ANTHROPIC_API_KEY",
            model_env="ANTHROPIC_REASONING_MODEL",
            probes={},
            path=persistence_paths["store"],
        )

        console, _ = _capture()
        dispatch_slash("/model toolcall set claude-haiku-4-5-20251001", Session(), console)

        assert "ANTHROPIC_TOOLCALL_MODEL=claude-haiku-4-5-20251001" in persistence_paths[
            "env"
        ].read_text(encoding="utf-8")
        stored = wizard_store.load_local_config(persistence_paths["store"])["targets"]["local"]
        assert stored["model"] == "claude-opus-4-7"

    def test_model_restore_updates_env_and_store(
        self,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: None)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")

        console, _ = _capture()
        dispatch_slash("/model restore anthropic", Session(), console)

        default_model = PROVIDER_BY_VALUE["anthropic"].default_model
        env_body = persistence_paths["env"].read_text(encoding="utf-8")
        assert f"ANTHROPIC_REASONING_MODEL={default_model}" in env_body

        stored = wizard_store.load_local_config(persistence_paths["store"])["targets"]["local"]
        assert stored["provider"] == "anthropic"
        assert stored["model"] == default_model

    def test_model_set_refuses_missing_credential_without_touching_files(
        self,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
        monkeypatch.setenv("LLM_PROVIDER", "openai")

        console, buf = _capture()
        dispatch_slash("/model set anthropic", Session(), console)

        assert "missing credential for anthropic" in buf.getvalue()
        assert not persistence_paths["env"].exists()
        assert not persistence_paths["store"].exists()

    def test_model_set_unknown_model_does_not_persist(
        self,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash("/model set anthropic not-a-real-model-xyz", Session(), console)

        assert "unknown model for anthropic" in buf.getvalue()
        assert not persistence_paths["env"].exists()
        assert not persistence_paths["store"].exists()

    @pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
    def test_model_set_accepts_and_persists_gpt56_tiers(
        self,
        model: str,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # #3931 acceptance criterion: ``/model set openai gpt-5.6-<tier>``
        # is accepted and persists. openai sets ``allow_custom_models``, so
        # this already held before the quick-picks landed; the test pins it
        # against a future tightening of the allowlist.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash(f"/model set openai {model}", Session(), console)

        assert "unknown model" not in buf.getvalue()
        assert f"OPENAI_REASONING_MODEL={model}" in persistence_paths["env"].read_text(
            encoding="utf-8"
        )
        stored = wizard_store.load_local_config(persistence_paths["store"])
        assert stored["targets"]["local"]["model"] == model

    def test_unavailable_ollama_model_does_not_persist(
        self,
        persistence_paths: dict[str, Path],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "surfaces.interactive_shell.command_registry.model.ollama.list_available_models",
            lambda _host: ("llama3.2:latest",),
        )
        console, output = _capture()

        dispatch_slash("/model set ollama ghost-model", Session(), console)

        assert "Ollama model is not available: ghost-model" in output.getvalue()
        assert not persistence_paths["env"].exists()
        assert not persistence_paths["store"].exists()
