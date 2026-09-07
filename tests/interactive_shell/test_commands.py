"""Tests for slash command dispatch."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from prompt_toolkit.history import FileHistory
from rich.console import Console

from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, dispatch_slash
from surfaces.interactive_shell.command_registry import repl_data as repl_data_module
from surfaces.interactive_shell.command_registry.tasks_cmds import _validate_cancel_args
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.tables.tool_catalog import ToolCatalogEntry


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


class TestDispatchSlash:
    def test_exit_returns_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "surfaces.interactive_shell.command_registry.system._flush_analytics_on_exit",
            lambda _console: None,
        )
        session = Session()
        console, _ = _capture()
        assert dispatch_slash("/exit", session, console) is False
        assert dispatch_slash("/quit", session, console) is False

    def test_exit_flushes_analytics_before_goodbye(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def _flush(console: Console) -> None:
            calls.append("flush")

        monkeypatch.setattr(
            "surfaces.interactive_shell.command_registry.system._flush_analytics_on_exit",
            _flush,
        )
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/quit", session, console) is False
        assert calls == ["flush"]
        assert "goodbye." in buf.getvalue()

    def test_delegated_cli_failure_does_not_exit_repl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-zero delegated CLI exit must not propagate False from dispatch_slash."""
        from surfaces.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            capture_output: bool,
            text: bool,
            encoding: str,
            errors: str,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, timeout, text, encoding, errors, env
            assert capture_output is True
            return subprocess.CompletedProcess(cmd, 1, stdout="not logged in\n", stderr="")

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/auth status", session, console) is True
        assert "non-zero code 1" in buf.getvalue()
        latest = session.history[-1]
        assert latest["type"] == "slash"
        assert latest["text"] == "/auth status"
        assert latest["ok"] is False

    def test_delegated_cli_timeout_does_not_exit_repl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Timed-out delegated CLI must not propagate False from dispatch_slash."""
        from surfaces.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, env
            assert timeout == m._UPDATE_SUBPROCESS_TIMEOUT_SECONDS
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout or 0.0)

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/update", session, console) is True
        assert "timed out" in buf.getvalue()
        assert session.history[-1]["ok"] is False

    def test_help_lists_all_commands(self) -> None:
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/help", session, console) is True
        output = buf.getvalue()
        for name in SLASH_COMMANDS:
            assert name in output
        assert "Use /help <command> for usage." in output
        assert "/model set <provider>" not in output
        latest = session.history[-1]
        assert latest["type"] == "slash"
        assert latest["text"] == "/help"
        assert latest.get("response_text") == "slash /help (succeeded)"
        assert "/model set" not in latest.get("response_text", "")

    def test_question_mark_shortcut_runs_help(self) -> None:
        """`/?` is the canonical shortcut for `/help` (vim / less convention)."""
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/?", session, console) is True
        output = buf.getvalue()
        # Any slash command name suffices as proof the help table rendered.
        assert "/help" in output
        assert "/tools" in output

    def test_help_command_detail_shows_usage(self) -> None:
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/help /model", session, console) is True
        output = buf.getvalue()
        assert "Show or change active LLM settings." in output
        assert "/model set <provider>" in output
        assert "Typed bare /model opens an interactive menu" in output
        from surfaces.interactive_shell.command_registry.model.command import COMMANDS as model_cmds

        notes = " ".join(model_cmds[0].notes)
        assert "slash_invoke" in notes
        assert "shows current settings" in notes

    def test_help_category_shows_compact_section(self) -> None:
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/help tasks", session, console) is True
        output = buf.getvalue()
        assert "Tasks commands" in output
        assert "/tasks" in output
        assert "/cancel <task_id>" not in output

    def test_tty_help_dispatch_uses_interactive_picker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from surfaces.interactive_shell.command_registry import help as help_cmd

        session = Session()
        console, buf = _capture()
        picker_called: list[bool] = []
        monkeypatch.setattr(help_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(
            help_cmd, "choose_help_command", lambda _sections: picker_called.append(True)
        )

        assert dispatch_slash("/help", session, console) is True

        assert picker_called == [True]
        assert buf.getvalue() == ""

    def test_bare_slash_previews_all_commands(self) -> None:
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/", session, console) is True
        output = buf.getvalue()
        assert "Slash commands" in output
        assert "/help" in output
        assert "/tools" in output
        assert "unknown command" not in output

    def test_trust_toggle(self) -> None:
        session = Session()
        console, _ = _capture()
        assert session.terminal.trust_mode is False
        dispatch_slash("/trust", session, console)
        assert session.terminal.trust_mode is True
        dispatch_slash("/trust off", session, console)
        assert session.terminal.trust_mode is False

    def test_auto_sets_med(self) -> None:
        session = Session()
        console, buf = _capture()
        dispatch_slash("/auto med", session, console)
        assert session.terminal.auto_level == "med"
        assert "Auto (Med)" in buf.getvalue()
        assert "allow reversible commands" in buf.getvalue()

    def test_auto_bare_shows_default_and_levels(self) -> None:
        session = Session()
        console, buf = _capture()
        dispatch_slash("/auto", session, console)
        out = buf.getvalue()
        assert "Auto (High)" in out
        assert "default:" in out
        assert "off" in out and "all actions require approval" in out
        assert "/trust on skips approval" in out

    def test_effort_sets_session_preference(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeLLM:
            provider = "openai"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())
        session = Session()
        console, buf = _capture()

        dispatch_slash("/effort max", session, console)

        assert session.reasoning_effort == "max"
        output = buf.getvalue()
        assert "reasoning effort set to" in output
        assert "runtime: xhigh" in output

    def test_effort_rejects_unknown_value(self) -> None:
        session = Session()
        console, buf = _capture()

        dispatch_slash("/effort turbo", session, console)

        assert session.reasoning_effort is None
        assert "unknown reasoning effort" in buf.getvalue()

    def test_effort_shows_default_config_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _FakeLLM:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4-7"
            anthropic_toolcall_model = "claude-haiku-4-5-20251001"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())
        session = Session()
        console, buf = _capture()

        dispatch_slash("/effort", session, console)

        output = buf.getvalue()
        assert "reasoning effort:" in output
        assert "(default)" in output
        assert "default config:" in output
        assert "anthropic does not use reasoning-effort overrides" in output

    def test_new_clears_session(self) -> None:
        session = Session()
        session.record("alert", "test")
        session.terminal.trust_mode = True
        console, _ = _capture()

        dispatch_slash("/new", session, console)

        assert session.history == []
        assert session.terminal.trust_mode is True  # /new keeps trust mode

    def test_status_shows_session_fields(self) -> None:
        session = Session()
        session.record("alert", "hello")
        session.reasoning_effort = "max"
        console, buf = _capture()
        dispatch_slash("/status", session, console)
        output = buf.getvalue()
        assert "interactions" in output
        assert "reasoning effort" in output
        assert "trust mode" in output
        assert "grounding cli cache" in output
        assert "grounding docs cache" in output

    def test_unknown_command_does_not_exit(self) -> None:
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/made-up", session, console) is True
        assert "Unknown command" in buf.getvalue()

    def test_unknown_command_suggests_close_match(self) -> None:
        session = Session()
        console, buf = _capture()
        assert dispatch_slash("/modle", session, console) is True
        output = buf.getvalue()
        assert "Unknown command" in output
        assert "Did you mean" in output
        assert "/model" in output

    def test_local_llm_is_not_a_builtin_slash_action(self) -> None:
        assert "/local-llm" not in SLASH_COMMANDS
        assert "/local_llm" not in SLASH_COMMANDS

    def test_empty_input_is_noop(self) -> None:
        session = Session()
        console, _ = _capture()
        assert dispatch_slash("   ", session, console) is True

    def test_history_shows_persisted_prompt_history(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        history = FileHistory(str(tmp_path / "interactive_history"))
        history.store_string("opensre health")
        history.store_string("/integrations list")

        session = Session()
        session.record("alert", "current session only")
        console, buf = _capture()

        assert dispatch_slash("/history", session, console) is True
        output = buf.getvalue()
        assert "Command history" in output
        assert "opensre health" in output
        assert "/integrations list" in output
        assert "current session only" not in output


class TestSpecificListCommands:
    """Coverage for /integrations list, /mcp list, /model show, and /tools list."""

    _FAKE_INTEGRATIONS = [
        {"service": "datadog", "source": "store", "status": "ok", "detail": "API ok"},
        {"service": "slack", "source": "env", "status": "failed", "detail": "No bot token"},
        {"service": "github", "source": "store", "status": "ok", "detail": "MCP ok"},
    ]

    def _patch_verify(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            lambda: list(self._FAKE_INTEGRATIONS),
        )

    def test_integrations_list_includes_mcp_services(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations list", Session(), console)
        output = buf.getvalue()
        assert "datadog" in output
        assert "slack" in output
        assert "github" in output

    def test_mcp_list_shows_only_mcp_services(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp list", Session(), console)
        output = buf.getvalue()
        assert "github" in output
        assert "datadog" not in output

    def _patch_llm(self, monkeypatch: object) -> None:
        """Provide a stable fake LLMSettings so the test doesn't depend on env."""

        class _FakeLLM:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4"
            anthropic_toolcall_model = "claude-haiku-4"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())

    def test_model_show_displays_provider_and_models(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model show", Session(), console)
        output = buf.getvalue()
        assert "provider" in output
        assert "reasoning model" in output
        assert "toolcall model" in output
        assert "anthropic" in output

    def test_model_show_displays_ollama_model(self, monkeypatch: object) -> None:
        class _FakeLLM:
            provider = "ollama"
            ollama_model = "qwen2.5:7b"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _FakeLLM())
        console, buf = _capture()
        dispatch_slash("/model show", Session(), console)
        output = buf.getvalue()
        assert "ollama" in output
        assert "qwen2.5:7b" in output
        assert "default" not in output

    def test_model_show_handles_missing_env_gracefully(self, monkeypatch: object) -> None:
        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: None)
        console, buf = _capture()
        dispatch_slash("/model show", Session(), console)
        assert "LLM settings unavailable" in buf.getvalue()

    def test_model_provider_menu_starts_focused_then_other(self, monkeypatch: object) -> None:
        from surfaces.interactive_shell.command_registry.model import command as model_cmd
        from surfaces.shared.llm_setup.provider_choices import FOCUSED_SETUP_PROVIDER_VALUES

        monkeypatch.setenv("LLM_PROVIDER", "anthropic")

        primary_values = [value for value, _label in model_cmd._provider_menu_choices()]
        other_values = [value for value, _label in model_cmd._other_provider_menu_choices()]

        assert primary_values == [
            *FOCUSED_SETUP_PROVIDER_VALUES,
            model_cmd.OTHER_PROVIDER_SELECTION,
        ]
        assert "anthropic" not in primary_values
        assert "anthropic" in other_values
        assert not set(FOCUSED_SETUP_PROVIDER_VALUES).intersection(other_values)

    def test_integrations_list_empty_prints_onboarding_hint(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            list,  # callable returning []
        )
        console, buf = _capture()
        dispatch_slash("/integrations list", Session(), console)
        assert "opensre integrations setup" in buf.getvalue()

    def test_tools_list_prints_registered_tools(self, monkeypatch: object) -> None:
        from surfaces.interactive_shell.command_registry import tools_cmds as tools_cmd_module

        monkeypatch.setattr(
            tools_cmd_module,
            "build_tool_catalog",
            lambda: [
                ToolCatalogEntry(
                    name="search_github",
                    surfaces=("chat", "action"),
                    description="Search GitHub code.",
                    source_file="tools/search_github.py",
                    input_schema_summary="query: string",
                )
            ],
        )

        console, buf = _capture()
        dispatch_slash("/tools list", Session(), console)
        output = buf.getvalue()
        assert "search_github" in output
        assert "chat" in output
        assert "Search GitHub code." in output


# ---------------------------------------------------------------------------
# Task 3 — Click-shadowing commands
# ---------------------------------------------------------------------------


class TestIntegrationsCommand:
    _FAKE = [
        {"service": "datadog", "source": "env", "status": "ok", "detail": "ok"},
        {"service": "slack", "source": "env", "status": "missing", "detail": "no token"},
        {"service": "github", "source": "store", "status": "ok", "detail": "MCP ok"},
    ]

    def _patch(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            lambda: list(self._FAKE),
        )

    def test_list_shows_all_services_including_github(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations list", Session(), console)
        output = buf.getvalue()
        assert "datadog" in output
        assert "github" in output

    def test_list_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations", Session(), console)
        assert "datadog" in buf.getvalue()

    def test_verify_reports_issues(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations verify", Session(), console)
        assert "need attention" in buf.getvalue()
        # The summary must name the fix, not just the count.
        assert "/integrations setup" in buf.getvalue()

    def test_verify_all_ok(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            lambda: [
                {"service": "datadog", "source": "env", "status": "ok", "detail": "ok"},
            ],
        )
        console, buf = _capture()
        dispatch_slash("/integrations verify", Session(), console)
        assert "all integrations ok" in buf.getvalue()

    def test_verify_via_slash_command(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/verify", Session(), console)
        assert "need attention" in buf.getvalue()

    def test_verify_one_service_via_slash_command(self, monkeypatch: object) -> None:
        verified: list[str] = []

        def _verify_one(service: str) -> dict[str, str]:
            verified.append(service)
            return {
                "service": service,
                "source": "env",
                "status": "ok",
                "detail": "ok",
            }

        monkeypatch.setattr(
            "integrations.registry.SUPPORTED_VERIFY_SERVICES",
            ("datadog",),
        )
        monkeypatch.setattr(repl_data_module, "verify_integration", _verify_one)
        console, buf = _capture()
        dispatch_slash("/verify datadog", Session(), console)
        assert verified == ["datadog"]
        assert "datadog ok" in buf.getvalue()

    def test_verify_unsupported_service(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            "integrations.registry.SUPPORTED_VERIFY_SERVICES",
            ("datadog",),
        )
        session = Session()
        console, buf = _capture()
        dispatch_slash("/verify not_a_real_service", session, console)
        assert "unsupported verify target" in buf.getvalue()
        assert session.history[-1]["ok"] is False

    def test_verify_servicenow_is_supported_target(self, monkeypatch: object) -> None:
        # Regression for #3102: servicenow must pass the real
        # SUPPORTED_VERIFY_SERVICES gate so "Is ServiceNow configured?"
        # executes the verifier instead of "unsupported verify target".
        verified: list[str] = []

        def _verify_one(service: str) -> dict[str, str]:
            verified.append(service)
            return {
                "service": service,
                "source": "local store",
                "status": "passed",
                "detail": "Configured for ServiceNow at https://dev12345.service-now.com.",
            }

        monkeypatch.setattr(repl_data_module, "verify_integration", _verify_one)
        session = Session()
        console, buf = _capture()
        dispatch_slash("/verify servicenow", session, console)
        assert verified == ["servicenow"]
        assert "servicenow" in buf.getvalue()
        assert session.history[-1]["ok"] is True

    def test_verify_one_service_via_integrations(self, monkeypatch: object) -> None:
        verified: list[str] = []

        def _verify_one(service: str) -> dict[str, str]:
            verified.append(service)
            return {
                "service": service,
                "source": "env",
                "status": "ok",
                "detail": "ok",
            }

        monkeypatch.setattr(repl_data_module, "verify_integration", _verify_one)
        console, buf = _capture()
        dispatch_slash("/integrations verify datadog", Session(), console)
        assert verified == ["datadog"]
        assert "datadog ok" in buf.getvalue()

    def test_show_known_service(self, monkeypatch: object) -> None:
        verified: list[str | None] = []

        def _verify_one(service: str) -> dict[str, str]:
            verified.append(service)
            return {
                "service": service,
                "source": "env",
                "status": "ok",
                "detail": "ok",
            }

        monkeypatch.setattr(
            repl_data_module,
            "configured_integration_names",
            lambda: ["datadog"],
        )
        monkeypatch.setattr(repl_data_module, "verify_integration", _verify_one)
        console, buf = _capture()
        dispatch_slash("/integrations show datadog", Session(), console)
        assert verified == ["datadog"]
        assert "datadog" in buf.getvalue()

    def test_show_unknown_service(self, monkeypatch: object) -> None:
        monkeypatch.setattr(repl_data_module, "configured_integration_names", lambda: ["datadog"])
        session = Session()
        session.record("slash", "/integrations show bogus")
        console, buf = _capture()
        dispatch_slash("/integrations show bogus", session, console)
        assert "service not found" in buf.getvalue()
        assert session.history[-1]["ok"] is False

    def test_show_missing_arg(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations show", Session(), console)
        assert "usage" in buf.getvalue()

    def test_unknown_subcommand_prints_hint(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations bogus", Session(), console)
        assert "unknown subcommand" in buf.getvalue()

    def test_setup_delegates_to_cli(self, monkeypatch: object) -> None:
        from surfaces.interactive_shell.command_registry import integrations as m

        captured = []
        monkeypatch.setattr(
            m, "run_cli_command", lambda _, args, **_kw: (captured.append(args), True)[1]
        )
        dispatch_slash("/integrations setup", Session(), Console())
        assert captured == [["integrations", "setup"]]

    def test_remove_uses_native_store_removal(self, monkeypatch: object) -> None:
        import infrastructure.analytics.capture as analytics_cli
        import integrations.store as store
        from surfaces.interactive_shell.command_registry import integrations as m

        removed: list[str] = []
        monkeypatch.setattr(m, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(m, "repl_choose_one", lambda **_: "yes")
        monkeypatch.setattr(store, "remove_integration", lambda svc: (removed.append(svc), True)[1])
        monkeypatch.setattr(analytics_cli, "capture_integration_removed", lambda *_: None)
        dispatch_slash("/integrations remove slack", Session(), Console())
        assert removed == ["slack"]

    def test_remove_cancelled_does_not_touch_store(self, monkeypatch: object) -> None:
        import integrations.store as store
        from surfaces.interactive_shell.command_registry import integrations as m

        removed: list[str] = []
        monkeypatch.setattr(m, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(m, "repl_choose_one", lambda **_: "no")
        monkeypatch.setattr(store, "remove_integration", lambda svc: (removed.append(svc), True)[1])
        dispatch_slash("/integrations remove slack", Session(), Console())
        assert removed == []


class TestMcpCommand:
    _FAKE = [
        {"service": "github", "source": "store", "status": "ok", "detail": "MCP ok"},
    ]

    def _patch(self, monkeypatch: object) -> None:
        monkeypatch.setattr(
            repl_data_module,
            "load_verified_integrations",
            lambda: list(self._FAKE),
        )

    def test_list_shows_mcp_services(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp list", Session(), console)
        assert "github" in buf.getvalue()

    def test_list_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp", Session(), console)
        assert "github" in buf.getvalue()

    def test_connect_delegates_to_cli(self, monkeypatch: object) -> None:
        from surfaces.interactive_shell.command_registry import integrations as m

        captured = []
        monkeypatch.setattr(
            m, "run_cli_command", lambda _, args, **_kw: (captured.append(args), True)[1]
        )
        dispatch_slash("/mcp connect", Session(), Console())
        assert captured == [["integrations", "setup"]]

    def test_disconnect_uses_native_store_removal(self, monkeypatch: object) -> None:
        import infrastructure.analytics.capture as analytics_cli
        import integrations.store as store
        from surfaces.interactive_shell.command_registry import integrations as m

        removed: list[str] = []
        monkeypatch.setattr(m, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(m, "repl_choose_one", lambda **_: "yes")
        monkeypatch.setattr(store, "remove_integration", lambda svc: (removed.append(svc), True)[1])
        monkeypatch.setattr(analytics_cli, "capture_integration_removed", lambda *_: None)
        dispatch_slash("/mcp disconnect github", Session(), Console())
        assert removed == ["github"]

    def test_unknown_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp bogus", Session(), console)
        assert "unknown subcommand" in buf.getvalue()


class TestModelCommand:
    def _patch_llm(self, monkeypatch: object) -> None:
        class _Fake:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4"
            anthropic_toolcall_model = "claude-haiku-4"

        monkeypatch.setattr(repl_data_module, "load_llm_settings", lambda: _Fake())

    def _redirect_wizard_store(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> Path:
        import config.setup_store as wizard_store

        store_path = tmp_path / "opensre.json"
        monkeypatch.setattr(wizard_store, "get_store_path", lambda: store_path)
        return store_path

    def test_show_displays_model_info(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model show", Session(), console)
        assert "anthropic" in buf.getvalue()

    def test_show_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model", Session(), console)
        assert "anthropic" in buf.getvalue()

    def test_model_interactive_set_flow_applies_selection(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync
        from surfaces.interactive_shell.command_registry.model import command as model_cmd

        env_path = tmp_path / ".env"
        store_path = self._redirect_wizard_store(monkeypatch, tmp_path)
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr(model_cmd, "repl_tty_interactive", lambda: True)
        selections = iter(
            ["set", model_cmd.OTHER_PROVIDER_SELECTION, "anthropic", "__provider_default__"]
        )
        monkeypatch.setattr(model_cmd, "repl_choose_one", lambda **_: next(selections))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        session = Session()
        session.terminal.exclusive_stdin_active = True
        dispatch_slash("/model", session, console)

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "reasoning model:" in output
        assert "LLM_PROVIDER=anthropic" in env_path.read_text(encoding="utf-8")
        stored = json.loads(store_path.read_text(encoding="utf-8"))
        assert stored["targets"]["local"]["provider"] == "anthropic"

    def test_model_interactive_show_then_done_shows_table_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._patch_llm(monkeypatch)
        from surfaces.interactive_shell.command_registry.model import command as model_cmd

        monkeypatch.setattr(model_cmd, "repl_tty_interactive", lambda: True)
        picks = iter(["show", "done"])
        monkeypatch.setattr(model_cmd, "repl_choose_one", lambda **_: next(picks))
        console, buf = _capture()
        session = Session()
        session.terminal.exclusive_stdin_active = True
        dispatch_slash("/model", session, console)
        assert "anthropic" in buf.getvalue()

    def test_bare_model_without_exclusive_stdin_shows_table_not_menu(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Agent slash_invoke path: TTY but no exclusive stdin → show, not picker."""
        self._patch_llm(monkeypatch)
        from surfaces.interactive_shell.command_registry.model import command as model_cmd

        monkeypatch.setattr(model_cmd, "repl_tty_interactive", lambda: True)
        menu_calls: list[str] = []
        monkeypatch.setattr(
            model_cmd,
            "_interactive_model_menu",
            lambda *_a, **_k: menu_calls.append("menu") or True,
        )
        console, buf = _capture()
        session = Session()
        assert session.terminal.exclusive_stdin_active is False
        dispatch_slash("/model", session, console)
        assert menu_calls == []
        assert "anthropic" in buf.getvalue()

    def test_model_interactive_escape_backs_out_without_changes(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        self._patch_llm(monkeypatch)
        from surfaces.interactive_shell.command_registry.model import command as model_cmd

        monkeypatch.setattr(model_cmd, "repl_tty_interactive", lambda: True)
        selections = iter(
            [
                "set",  # root -> set
                model_cmd.OTHER_PROVIDER_SELECTION,  # provider submenu selected
                "anthropic",  # provider selected
                None,  # Esc from model selection -> back to provider list
                None,  # Esc from provider list -> back to root action list
                None,  # Esc at root -> close menu
            ]
        )
        monkeypatch.setattr(model_cmd, "repl_choose_one", lambda **_: next(selections))
        session = Session()
        session.terminal.exclusive_stdin_active = True
        console, buf = _capture()
        dispatch_slash("/model", session, console)

        assert "switched LLM provider" not in buf.getvalue()
        assert session.history[-1]["ok"] is True

    def test_set_switches_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", tmp_path / ".env")
        store_path = self._redirect_wizard_store(monkeypatch, tmp_path)
        reset_calls: list[str] = []
        monkeypatch.setattr(
            "core.llm.factory.reset_llm_clients", lambda: reset_calls.append("reset")
        )
        # /model set now refuses to half-update .env when the target provider
        # has no usable credential; supply one so the happy path still runs.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash("/model set anthropic", Session(), console)

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "anthropic" in output
        # Reviewer (#1192) couldn't tell from "anthropic (X)" which slot the
        # model went into; the success message must now explicitly label the
        # reasoning slot and name the env var it lands in.
        assert "reasoning model:" in output
        assert "ANTHROPIC_REASONING_MODEL" in output
        assert "LLM_PROVIDER=anthropic" in (tmp_path / ".env").read_text(encoding="utf-8")
        stored = json.loads(store_path.read_text(encoding="utf-8"))
        assert stored["targets"]["local"]["provider"] == "anthropic"
        assert reset_calls == ["reset"]

    def test_set_refuses_when_credential_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """If prompt-safe status has no credential path, /model set must not
        touch .env or os.environ."""
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        store_path = self._redirect_wizard_store(monkeypatch, tmp_path)
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("OPENSRE_LLM_AUTH_METADATA_PATH", str(tmp_path / "llm-auth.json"))
        # Keyring lookups in CI / sandboxes are flaky; force the helper into
        # the env-only path so the test is deterministic.
        monkeypatch.setenv("OPENSRE_DISABLE_KEYRING", "1")
        # LLM_PROVIDER must not be rewritten by a rejected switch — capture
        # what it was before so we can assert it is unchanged.
        monkeypatch.setenv("LLM_PROVIDER", "gemini")

        console, buf = _capture()
        dispatch_slash("/model set anthropic", Session(), console)

        output = buf.getvalue()
        assert "missing credential for anthropic" in output
        assert "ANTHROPIC_API_KEY" in output
        assert "switched LLM provider" not in output
        # No .env should have been written.
        assert not env_path.exists()
        assert not store_path.exists()
        # And the live LLM_PROVIDER must be untouched.
        import os

        assert os.environ.get("LLM_PROVIDER") == "gemini"

    def test_set_missing_provider_prints_usage(self) -> None:
        console, buf = _capture()
        dispatch_slash("/model set", Session(), console)
        assert "usage" in buf.getvalue()

    def test_set_unknown_reasoning_model_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        session = Session()
        session.record("slash", "/model set anthropic not-a-real-model-xyz")

        console, buf = _capture()
        dispatch_slash("/model set anthropic not-a-real-model-xyz", session, console)

        output = buf.getvalue()
        assert "unknown model for anthropic" in output
        assert "not-a-real-model-xyz" in output
        assert "switched LLM provider" not in output
        assert not env_path.exists()
        assert session.history[-1]["ok"] is False

    def test_set_custom_reasoning_model_is_accepted_for_openai(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash("/model set openai gpt-5.5", Session(), console)

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "gpt-5.5" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=openai" in contents
        assert "OPENAI_REASONING_MODEL=gpt-5.5" in contents
        assert "OPENAI_MODEL=gpt-5.5" in contents

    def test_set_bare_model_updates_active_provider_reasoning_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        store_path = self._redirect_wizard_store(monkeypatch, tmp_path)
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("LLM_PROVIDER", "openai")

        console, buf = _capture()
        dispatch_slash("/model set gpt-5.5", Session(), console)

        output = buf.getvalue()
        assert "reasoning model set to" in output
        assert "gpt-5.5" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=" not in contents
        assert "OPENAI_REASONING_MODEL=gpt-5.5" in contents
        assert "OPENAI_MODEL=gpt-5.5" in contents
        stored = json.loads(store_path.read_text(encoding="utf-8"))
        assert stored["targets"]["local"]["provider"] == "openai"
        assert stored["targets"]["local"]["model"] == "gpt-5.5"

    def test_set_bare_gpt_words_normalizes_to_model_id(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("LLM_PROVIDER", "openai")

        dispatch_slash("/model set gpt 5.5", Session(), _capture()[0])

        contents = env_path.read_text(encoding="utf-8")
        assert "OPENAI_REASONING_MODEL=gpt-5.5" in contents
        assert "OPENAI_MODEL=gpt-5.5" in contents

    def test_switch_reasoning_model_normalizes_whitespace_slug(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Regression: the planner ``llm_set_provider`` tool dispatches the raw
        target straight to ``switch_reasoning_model`` (no CLI arg-splitting), so a
        spoken "set model to gpt 5.5" arrived as the single token ``"gpt 5.5"``.
        Because openai allows custom models, that malformed slug used to be
        persisted verbatim and then silently fail availability checks. It must be
        normalized to ``gpt-5.5`` instead."""
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync
        from surfaces.interactive_shell.command_registry import switch_reasoning_model

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("LLM_PROVIDER", "openai")

        console, buf = _capture()
        ok = switch_reasoning_model("gpt 5.5", console)

        assert ok is True
        assert "gpt-5.5" in buf.getvalue()
        assert "gpt 5.5" not in buf.getvalue()
        contents = env_path.read_text(encoding="utf-8")
        assert "OPENAI_REASONING_MODEL=gpt-5.5" in contents
        assert "OPENAI_MODEL=gpt-5.5" in contents
        assert "gpt 5.5" not in contents

    def test_set_unknown_toolcall_model_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash(
            "/model set anthropic claude-opus-4-7 --toolcall-model not-a-real-model-xyz",
            Session(),
            console,
        )

        output = buf.getvalue()
        assert "unknown model for anthropic" in output
        assert "not-a-real-model-xyz" in output
        assert "switched LLM provider" not in output
        assert not env_path.exists()

    def test_set_with_toolcall_flag_writes_both_env_vars(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """`/model set <provider> [model] --toolcall-model <m>` must persist both."""
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash(
            "/model set anthropic claude-opus-4-7 --toolcall-model claude-opus-4-7",
            Session(),
            console,
        )

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "toolcall model" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=anthropic" in contents
        assert "ANTHROPIC_REASONING_MODEL=claude-opus-4-7" in contents
        assert "ANTHROPIC_TOOLCALL_MODEL=claude-opus-4-7" in contents

    def test_restore_resets_active_provider_to_default_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_REASONING_MODEL", "not-a-real-model-xyz")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        console, buf = _capture()
        dispatch_slash("/model restore", Session(), console)

        output = buf.getvalue()
        assert "switched LLM provider" in output
        assert "claude-opus-4-7" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "LLM_PROVIDER=anthropic" in contents
        assert "ANTHROPIC_REASONING_MODEL=claude-opus-4-7" in contents
        assert "ANTHROPIC_MODEL=claude-opus-4-7" in contents

    def test_set_unknown_flag_prints_usage(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash("/model set anthropic --made-up-flag x", Session(), console)
        output = buf.getvalue()
        assert "unknown flag" in output
        assert "--made-up-flag" in output
        assert "usage" in output

    def test_set_toolcall_flag_without_value_prints_specific_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Reviewer ask: a missing flag value must say *which* flag, not just
        echo the generic usage line."""
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash("/model set anthropic --toolcall-model", Session(), console)
        output = buf.getvalue()
        assert "missing value for --toolcall-model" in output
        # And we must not have written anything to .env on a parse failure.
        assert not env_path.exists() or "ANTHROPIC_TOOLCALL_MODEL" not in env_path.read_text()

    def test_toolcall_set_updates_only_toolcall_model(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """`/model toolcall set <m>` must persist only the toolcall env var."""
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        env_path = tmp_path / ".env"
        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", env_path)
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", env_path)
        reset_calls: list[str] = []
        monkeypatch.setattr(
            "core.llm.factory.reset_llm_clients", lambda: reset_calls.append("reset")
        )
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")

        console, buf = _capture()
        dispatch_slash("/model toolcall set claude-opus-4-7", Session(), console)

        output = buf.getvalue()
        assert "toolcall model set to" in output
        contents = env_path.read_text(encoding="utf-8")
        assert "ANTHROPIC_TOOLCALL_MODEL=claude-opus-4-7" in contents
        # Reasoning model is left untouched.
        assert "ANTHROPIC_REASONING_MODEL" not in contents
        # LLM_PROVIDER must not be rewritten by a toolcall-only switch.
        assert "LLM_PROVIDER=" not in contents
        assert reset_calls == ["reset"]

    def test_toolcall_set_missing_arg_prints_usage(self) -> None:
        console, buf = _capture()
        dispatch_slash("/model toolcall set", Session(), console)
        assert "usage" in buf.getvalue()

    def test_toolcall_set_for_codex_provider_is_rejected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Providers without a separate toolcall model (codex/claude-code/gemini-cli/ollama)
        must not silently accept toolcall overrides."""
        import surfaces.shared.llm_setup.env_sync as env_sync

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setenv("LLM_PROVIDER", "codex")
        console, buf = _capture()
        dispatch_slash("/model toolcall set gpt-5.4", Session(), console)
        assert "does not expose a separate toolcall model" in buf.getvalue()

    def test_switch_alias_switches_provider(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        self._patch_llm(monkeypatch)
        import surfaces.shared.llm_setup.env_sync as env_sync

        monkeypatch.setattr(env_sync, "PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setattr("config.env_file.PROJECT_ENV_PATH", tmp_path / ".env")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        console, buf = _capture()
        dispatch_slash("/model switch anthropic", Session(), console)

        assert "switched LLM provider" in buf.getvalue()

    def test_unknown_subcommand(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model bogus", Session(), console)
        assert "unknown subcommand" in buf.getvalue()


class TestVersionCommand:
    def test_shows_version_info(self) -> None:
        console, buf = _capture()
        dispatch_slash("/version", Session(), console)
        output = buf.getvalue()
        assert "opensre" in output
        assert "python" in output
        assert "os" in output


# Task 4 — Session-state commands


class TestResumeCommand:
    """Tests for /resume command — session adoption and context restoration."""

    def test_apply_resume_adopts_target_session_and_restores_context(self, tmp_path: Path) -> None:
        """_apply_resume_data must flush the current session, adopt the target ID,
        reopen its file, and restore cli_agent_messages + accumulated_context."""
        from unittest.mock import patch

        from core.agent_harness.session import (
            JsonlSessionStore,
            default_session_repo,
        )
        from surfaces.interactive_shell.command_registry.session_cmds import _apply_resume_data

        SessionState = JsonlSessionStore()
        session = Session()
        old_id = session.session_id
        target_id = "old-abc-1234567890"

        with patch(
            "core.agent_harness.session.persistence.paths.sessions_dir",
            return_value=tmp_path,
        ):
            SessionState.open_session(session)
            session.record("chat", "pre-resume turn")

            # Pre-create a finalized target session file to resume into.
            target_path = tmp_path / f"{target_id}.jsonl"
            target_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "session",
                                "version": 2,
                                "id": target_id,
                                "created_at": "2026-05-29T10:00:00+00:00",
                                "cwd": "",
                            }
                        ),
                        json.dumps(
                            {
                                "id": "entry1",
                                "parent_id": None,
                                "timestamp": "2026-05-29T10:00:01+00:00",
                                "type": "custom_message",
                                "custom_type": "turn_stub",
                                "kind": "chat",
                                "text": "hello",
                                "display": False,
                            }
                        ),
                        json.dumps(
                            {
                                "id": "entry2",
                                "parent_id": "entry1",
                                "timestamp": "2026-05-29T10:00:02+00:00",
                                "type": "message",
                                "role": "user",
                                "content": "hello",
                                "metadata": {"kind": "chat"},
                            }
                        ),
                        json.dumps(
                            {
                                "id": "entry3",
                                "parent_id": "entry2",
                                "timestamp": "2026-05-29T10:00:03+00:00",
                                "type": "message",
                                "role": "assistant",
                                "content": "hi",
                                "metadata": {"kind": "chat"},
                            }
                        ),
                        json.dumps(
                            {
                                "id": "entry4",
                                "parent_id": "entry3",
                                "timestamp": "2026-05-29T10:00:04+00:00",
                                "type": "custom_message",
                                "custom_type": "accumulated_context",
                                "content": {"service": "redis"},
                                "display": False,
                            }
                        ),
                        json.dumps(
                            {
                                "id": "entry5",
                                "parent_id": "entry4",
                                "timestamp": "2026-05-29T10:00:05+00:00",
                                "type": "leaf",
                                "total_turns": 1,
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            data = default_session_repo().load_session(target_id[:8])
            assert data is not None

            console, buf = _capture()
            slash_command = f"/resume {target_id[:8]}"
            result = _apply_resume_data(data, session, console, slash_command=slash_command)

            # Current (empty-ish) session file must be finalized without /resume turn
            old_records = [
                json.loads(line)
                for line in (tmp_path / f"{old_id}.jsonl").read_text().splitlines()
                if line.strip()
            ]
            assert old_records[-1]["type"] == "leaf"
            assert not any(
                r.get("kind") == "slash" for r in old_records if r.get("type") == "custom_message"
            )

            # Target session is reopened — slash turn recorded on resumed session
            assert session.session_id == target_id
            target_records = [
                json.loads(line)
                for line in target_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            assert target_records[0]["type"] == "session"
            turn_stubs = [
                r
                for r in target_records
                if r.get("type") == "custom_message" and r.get("custom_type") == "turn_stub"
            ]
            assert turn_stubs[-1]["kind"] == "slash"
            assert turn_stubs[-1]["text"].startswith("/resume")

        assert result is True
        assert session.session_id == target_id
        assert session.agent.messages == [("user", "hello"), ("assistant", "hi")]
        assert session.accumulated_context == {"service": "redis"}
        output = buf.getvalue()
        assert "resumed session" in output
        assert "old-abc" in output

    def test_apply_resume_noop_when_no_messages_or_context(self) -> None:
        """When the session has no conversation, _apply_resume_data must return
        early without rotating the session."""
        from surfaces.interactive_shell.command_registry.session_cmds import _apply_resume_data

        session = Session()
        old_id = session.session_id

        data: dict = {
            "session_id": "empty-sid",
            "name": "",
            "cli_agent_messages": [],
            "accumulated_context": {},
            "history": [],
            "turn_details": [],
            "has_snapshot": False,
        }
        console, buf = _capture()
        _apply_resume_data(data, session, console)

        assert session.session_id == old_id
        assert "no conversation to resume" in buf.getvalue()

    def test_apply_resume_displays_history_in_repl_format(self, tmp_path: Path) -> None:
        """History display uses REPL turn order and includes slash commands."""
        from unittest.mock import patch

        from core.agent_harness.session import JsonlSessionStore
        from surfaces.interactive_shell.command_registry.session_cmds import _apply_resume_data

        SessionState = JsonlSessionStore()
        data = {
            "session_id": "display-test-abc123456789",
            "name": "My Session",
            "cli_agent_messages": [
                ("user", "what is opensre?"),
                ("assistant", "OpenSRE is a tool"),
            ],
            "accumulated_context": {},
            "history": [
                {"type": "turn", "kind": "slash", "text": "/status"},
                {"type": "turn", "kind": "chat", "text": "what is opensre?"},
            ],
            "turn_details": [],
            "has_snapshot": True,
        }
        session = Session()
        console, buf = _capture()

        with patch(
            "core.agent_harness.session.persistence.paths.sessions_dir",
            return_value=tmp_path,
        ):
            SessionState.open_session(session)
            _apply_resume_data(data, session, console)

        output = buf.getvalue()
        assert "❯" in output
        assert "Ω" in output
        assert "$ /status" in output
        assert "you  " not in output
        assert "sre  " not in output
        assert "what is opensre?" in output
        assert "OpenSRE is a tool" in output

    def test_apply_resume_no_history_keeps_user_assistant_pairs_with_duplicate_prompts(
        self,
    ) -> None:
        """No-history rendering should not emit orphaned assistant blocks."""
        from surfaces.interactive_shell.command_registry.session_cmds import _apply_resume_data

        data = {
            "session_id": "display-no-history-abc123",
            "name": "No History",
            "cli_agent_messages": [
                ("user", "repeat"),
                ("assistant", "first answer"),
                ("user", "repeat"),
                ("assistant", "second answer"),
            ],
            "accumulated_context": {},
            "history": [],
            "turn_details": [],
            "has_snapshot": True,
        }

        session = Session()
        console, buf = _capture()
        _apply_resume_data(data, session, console)

        output = buf.getvalue()
        assert output.count("❯ repeat") == 2
        assert output.count("Ω") == 2
        assert "first answer" in output
        assert "second answer" in output

    def test_action_agent_llm_error_persisted_to_cli_agent_messages(self) -> None:
        """Action-agent LLM failures must be stored so /resume can show them."""
        from unittest.mock import patch

        from surfaces.interactive_shell.runtime.action_turn import (
            run_action_tool_turn,
        )

        session = Session()
        console, _ = _capture()

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("codex: quota or rate limit exceeded (exit 1)")

        with patch(
            "surfaces.interactive_shell.runtime.action_turn.default_llm_factory",
            side_effect=_raise,
        ):
            result = run_action_tool_turn("check cpu usage", session, console)

        # The error turn must be recorded in cli_agent_messages for /resume
        assert result.handled is True
        assert result.has_unhandled_clause is True
        assert len(session.agent.messages) == 2
        assert session.agent.messages[0] == ("user", "check cpu usage")
        assert session.agent.messages[1][0] == "assistant"
        assert "quota" in session.agent.messages[1][1]


class TestHistoryCommand:
    def test_empty_history_says_so(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        console, buf = _capture()
        dispatch_slash("/history", Session(), console)
        assert "no history" in buf.getvalue()

    def test_history_shows_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        history = FileHistory(str(tmp_path / "interactive_history"))
        history.store_string("pod crash in prod")
        history.store_string("/status")

        console, buf = _capture()
        dispatch_slash("/history", Session(), console)
        output = buf.getvalue()
        assert "Command history" in output
        assert "pod crash in prod" in output
        assert "/status" in output

    def test_history_ignores_session_only_entries(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import config.constants as const_module

        monkeypatch.setattr(const_module, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setattr("config.constants.paths.OPENSRE_HOME_DIR", tmp_path)
        session = Session()
        session.record("alert", "bad input", ok=False)
        console, buf = _capture()
        dispatch_slash("/history", session, console)
        output = buf.getvalue()
        assert "no history" in output
        assert "bad input" not in output


class TestContextCommand:
    def test_empty_context_says_so(self) -> None:
        console, buf = _capture()
        dispatch_slash("/context", Session(), console)
        assert "no infra context" in buf.getvalue()

    def test_shows_accumulated_keys(self) -> None:
        session = Session()
        session.accumulated_context = {"service": "orders-api", "region": "us-east-1"}
        console, buf = _capture()
        dispatch_slash("/context", session, console)
        output = buf.getvalue()
        assert "orders-api" in output
        assert "us-east-1" in output


class TestCostCommand:
    def test_no_token_data_shows_placeholder(self) -> None:
        console, buf = _capture()
        dispatch_slash("/cost", Session(), console)
        assert "no LLM usage recorded yet" in buf.getvalue()

    def test_shows_token_counts_when_available(self) -> None:
        session = Session()
        session.tokens.totals = {"input": 1000, "output": 500}
        session.tokens.call_count = 2
        console, buf = _capture()
        dispatch_slash("/cost", session, console)
        output = buf.getvalue()
        assert "1,000" in output
        assert "500" in output
        assert "llm calls" in output
        assert "2" in output

    def test_shows_estimate_labels_when_mixed(self) -> None:
        session = Session()
        session.tokens.totals = {
            "input": 400,
            "output": 60,
            "input_measured": 300,
            "output_measured": 40,
            "input_estimated": 100,
            "output_estimated": 20,
        }
        session.tokens.call_count = 2
        console, buf = _capture()
        dispatch_slash("/cost", session, console)
        output = buf.getvalue()
        assert "provider + 100 est." in output
        assert "provider + 20 est." in output
        assert "includes estimates" in output


class TestVerboseCommand:
    def test_on_sets_env_var(self, monkeypatch: object) -> None:
        import os

        monkeypatch.delenv("TRACER_VERBOSE", raising=False)  # type: ignore[attr-defined]
        console, buf = _capture()
        dispatch_slash("/verbose on", Session(), console)
        assert os.environ.get("TRACER_VERBOSE") == "1"
        assert "verbose logging on" in buf.getvalue()

    def test_off_removes_env_var(self, monkeypatch: object) -> None:
        import os

        monkeypatch.setenv("TRACER_VERBOSE", "1")  # type: ignore[attr-defined]
        console, buf = _capture()
        dispatch_slash("/verbose off", Session(), console)
        assert "TRACER_VERBOSE" not in os.environ
        assert "verbose logging off" in buf.getvalue()

    def test_no_arg_turns_on(self, monkeypatch: object) -> None:
        import os

        monkeypatch.delenv("TRACER_VERBOSE", raising=False)  # type: ignore[attr-defined]
        console, _ = _capture()
        dispatch_slash("/verbose", Session(), console)
        assert os.environ.get("TRACER_VERBOSE") == "1"


class TestCompactCommand:
    def test_nothing_to_compact_when_small(self) -> None:
        session = Session()
        session.agent.messages = [("user", f"m{i}") for i in range(4)]
        console, buf = _capture()
        dispatch_slash("/compact", session, console)
        assert "Nothing to compact yet." in buf.getvalue()
        assert len(session.agent.messages) == 4

    def test_compacts_conversation_branch_when_over_keep_limit(self) -> None:
        session = Session()
        session.agent.messages = [("user", f"message number {i}") for i in range(20)]
        console, buf = _capture()
        dispatch_slash("/compact", session, console)
        # compact_session_branch keeps the most recent 8 messages and prepends
        # a single summary message.
        assert len(session.agent.messages) == 9
        assert session.agent.messages[0][0] == "assistant"
        assert "Session summary" in session.agent.messages[0][1]
        assert "compacted session context" in buf.getvalue()
        assert any(
            entry.get("type") == "slash" and entry.get("text") == "/compact"
            for entry in session.history
        )


class TestCancelCommand:
    def test_usage_without_task_id(self) -> None:
        console, buf = _capture()
        dispatch_slash("/cancel", Session(), console)
        assert "usage" in buf.getvalue().lower()
        assert "/tasks" in buf.getvalue()


class TestPrePolicyValidation:
    """Regression for #1712: ``validate_args`` runs before the policy gate, so
    invalid args never trigger the ``Proceed?`` confirmation prompt."""

    def test_missing_arg_skips_policy_prompt(self) -> None:
        command = "/cancel"
        expected_usage_fragment = "/cancel <task_id>"
        confirm_calls: list[str] = []

        def _confirm(prompt: str) -> str:
            confirm_calls.append(prompt)
            return "n"

        session = Session()

        console, buf = _capture()
        dispatch_slash(command, session, console, confirm_fn=_confirm, is_tty=True)

        assert expected_usage_fragment in buf.getvalue()
        assert confirm_calls == [], f"confirm_fn must not be called for {command} with no args"
        latest = session.history[-1]
        assert latest["type"] == "slash"
        assert latest["text"] == command
        assert latest["ok"] is False
        assert latest["response_text"].startswith(f"slash {command} (failed)")
        assert expected_usage_fragment in latest["response_text"]

    def test_validate_args_fires_in_trust_mode(self) -> None:
        """Trust mode bypasses the policy prompt but must not bypass arg validation."""
        confirm_calls: list[str] = []

        def _confirm(prompt: str) -> str:
            confirm_calls.append(prompt)
            return "y"

        session = Session()
        session.terminal.trust_mode = True

        console, buf = _capture()
        dispatch_slash("/cancel", session, console, confirm_fn=_confirm, is_tty=True)

        assert "/cancel <task_id>" in buf.getvalue()
        assert confirm_calls == [], "trust mode must not skip arg validation"


class TestSlashValidatorFunctions:
    """Direct unit tests for the per-command pre-policy validators."""

    def test_returns_usage_when_args_empty(self) -> None:
        result = _validate_cancel_args([])
        assert isinstance(result, str)
        assert "/cancel <task_id>" in result

    def test_returns_none_when_args_present(self) -> None:
        assert _validate_cancel_args(["task-abc"]) is None


class TestRunCliCommand:
    """Regression: captured subprocess output must survive REPL prompt redraw."""

    def test_timed_delegate_streams_to_the_real_terminal_without_buffering(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A timeout alone must not force output capture.

        /update sets ``subprocess_timeout`` as a hang safety net, not to request
        buffering. Forcing capture here would swallow the install script's own
        live progress until the whole subprocess exits; letting it inherit the
        real TTY (like /onboard already does) keeps that progress visible live.
        """
        from surfaces.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check
            assert timeout == 30.0
            assert env["OPENSRE_PARENT_INTERACTIVE_SHELL"] == "1"
            assert cmd[:3] == [sys.executable, "-m", "surfaces.cli"]
            assert cmd[3:] == ["update"]
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, _buf = _capture()
        assert m.run_cli_command(console, ["update"], subprocess_timeout=30.0) is True

    def test_config_delegate_captures_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from surfaces.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            capture_output: bool,
            text: bool,
            encoding: str,
            errors: str,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, timeout, text, encoding, errors
            assert capture_output is True
            assert env["OPENSRE_PARENT_INTERACTIVE_SHELL"] == "1"
            assert cmd[:3] == [sys.executable, "-m", "surfaces.cli"]
            assert cmd[3:] == ["config", "show"]
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="Provider : cursor\n",
                stderr="",
            )

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, buf = _capture()
        assert m._cmd_config(Session(), console, ["show"]) is True
        assert "Provider : cursor" in buf.getvalue()

    def test_capture_output_replays_stdout_through_console_without_timeout(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``capture_output=True`` must send child stdout through ``console`` even
        when no timeout is set, so non-interactive slash commands like
        ``/guardrails list`` do not lose their output to the parent stdout FD.
        """
        from surfaces.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            capture_output: bool,
            text: bool,
            encoding: str,
            errors: str,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, text, encoding, errors
            assert capture_output is True
            assert env["OPENSRE_PARENT_INTERACTIVE_SHELL"] == "1"
            assert timeout is None
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="catalog row one\ncatalog row two\n",
                stderr="",
            )

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, buf = _capture()
        assert m.run_cli_command(console, ["guardrails", "list"], capture_output=True) is True
        assert "catalog row one" in buf.getvalue()
        assert "catalog row two" in buf.getvalue()

    def test_timeout_replays_decoded_partial_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from surfaces.interactive_shell.command_registry import cli_parity as m

        replayed: list[tuple[str, str | None]] = []

        def _fake_print_command_output(
            _console: Console,
            output: str,
            *,
            style: str | None = None,
            on_collapse: object = None,
        ) -> None:
            replayed.append((output, style))

        def _fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(
                cmd=[sys.executable, "-m", "cli", "update"],
                timeout=30.0,
                output=b"partial stdout\n",
                stderr=b"partial stderr\n",
            )

        monkeypatch.setattr(m, "print_command_output", _fake_print_command_output)
        monkeypatch.setattr(m.subprocess, "run", _fake_run)

        console, buf = _capture()
        assert m.run_cli_command(console, ["update"], subprocess_timeout=30.0) is True
        from infrastructure.terminal.theme import ERROR

        assert replayed == [("partial stdout\n", None), ("partial stderr\n", ERROR)]
        assert "timed out" in buf.getvalue()

    def test_interactive_session_keeps_repl_alive_on_subprocess_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Delegated CLI failures must not return False to dispatch_slash on the REPL."""
        from surfaces.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            capture_output: bool,
            text: bool,
            encoding: str,
            errors: str,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, timeout, text, encoding, errors, env
            assert capture_output is True
            return subprocess.CompletedProcess(cmd, 1, stdout="auth failed\n", stderr="")

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        session = Session()
        session.record("slash", "/auth status", ok=True)
        console, buf = _capture()
        assert (
            m.run_cli_command(
                console,
                ["auth", "status"],
                capture_output=True,
                session=session,
            )
            is True
        )
        assert "non-zero code 1" in buf.getvalue()
        assert session.history[-1]["ok"] is False

    def test_headless_session_propagates_subprocess_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Gateway/headless surfaces need the real exit status for slash analytics."""
        from core.agent_harness.session import SessionCore
        from core.agent_harness.session.persistence.memory import InMemorySessionStore
        from surfaces.interactive_shell.command_registry import cli_parity as m

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None,
            capture_output: bool,
            text: bool,
            encoding: str,
            errors: str,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, text, encoding, errors, env
            assert capture_output is True
            assert timeout == m._HEADLESS_CLI_SUBPROCESS_TIMEOUT_SECONDS
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom\n")

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        session = SessionCore(store=InMemorySessionStore())
        session.record("slash", "/remote health", ok=True)
        console, _buf = _capture()
        assert m.run_cli_command(console, ["remote", "health"], session=session) is False
        assert session.history[-1]["ok"] is False

    def test_frozen_binary_delegate_reexecs_opensre_without_module_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Release binaries have ``sys.executable`` set to ``opensre`` itself.

        Passing ``-m cli`` to that executable sends ``-m`` to Click and fails
        before slash commands like ``/onboard`` can run.
        """
        from surfaces.interactive_shell.command_registry import cli_parity as m
        from tools.interactive_shell import cli as opensre_cli

        captured: list[list[str]] = []

        monkeypatch.setattr(opensre_cli.sys, "executable", "/tmp/opensre")
        monkeypatch.setattr(opensre_cli.sys, "frozen", True, raising=False)

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None = None,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, timeout
            assert env["OPENSRE_PARENT_INTERACTIVE_SHELL"] == "1"
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, _ = _capture()

        assert m.run_cli_command(console, ["onboard"], capture_output=False) is True
        assert captured == [["/tmp/opensre", "onboard"]]

    def test_script_entrypoint_delegate_reuses_opensre_without_module_flags(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Console-script launchers should be re-entered as ``opensre ...``.

        Some installed launchers expose a Python-looking ``sys.executable`` but
        pass ``-m`` through to Click. Reusing the current ``opensre`` entrypoint
        avoids turning ``/onboard`` into ``opensre -m cli onboard``.
        """
        from surfaces.interactive_shell.command_registry import cli_parity as m
        from tools.interactive_shell import cli as opensre_cli

        captured: list[list[str]] = []

        monkeypatch.setattr(opensre_cli.sys, "argv", ["/tmp/bin/opensre"])
        monkeypatch.setattr(opensre_cli.sys, "executable", "/tmp/bin/python3")
        monkeypatch.setattr(opensre_cli.sys, "frozen", False, raising=False)

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None = None,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, timeout
            assert env["OPENSRE_PARENT_INTERACTIVE_SHELL"] == "1"
            captured.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, _ = _capture()

        assert m.run_cli_command(console, ["onboard"], capture_output=False) is True
        assert captured == [["/tmp/bin/opensre", "onboard"]]

    def test_cli_delegate_marks_parent_interactive_shell(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from surfaces.interactive_shell.command_registry import cli_parity as m

        captured_envs: list[dict[str, str]] = []

        def _fake_run(
            cmd: list[str],
            *,
            check: bool,
            timeout: float | None = None,
            env: dict[str, str],
        ) -> subprocess.CompletedProcess[str]:
            del check, timeout
            captured_envs.append(env)
            return subprocess.CompletedProcess(cmd, 0)

        monkeypatch.setattr(m.subprocess, "run", _fake_run)
        console, _ = _capture()

        assert m.run_cli_command(console, ["onboard"], capture_output=False) is True
        assert captured_envs[0]["OPENSRE_PARENT_INTERACTIVE_SHELL"] == "1"


class TestCliDelegatedCommands:
    """Coverage for commands that simply delegate to the underlying Click CLI."""

    @pytest.mark.parametrize(
        "command,expected_args",
        [
            ("/config show", ["config", "show"]),
            ("/remote health", ["remote", "health"]),
            ("/guardrails audit", ["guardrails", "audit"]),
            ("/update", ["update"]),
            ("/uninstall", ["uninstall"]),
        ],
    )
    def test_command_delegation(
        self, monkeypatch: object, command: str, expected_args: list[str]
    ) -> None:
        from surfaces.interactive_shell.command_registry import cli_parity as m

        captured: list[list[str]] = []

        def _fake_run_cli_command(_console: Console, args: list[str], **kwargs: object) -> bool:
            captured.append(args)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)
        dispatch_slash(command, Session(), Console())
        assert captured == [expected_args]

    def test_slash_onboard_delegates_to_run_cli_command(self, monkeypatch: object) -> None:
        """``/onboard`` must delegate to ``run_cli_command`` so the wizard runs
        with inherited stdin. The REPL loop guarantees exclusive stdin for
        ``/onboard`` via ``runtime.input_policy``, so
        the wizard's prompt_toolkit Application no longer conflicts with the
        shell's active one.
        """
        from surfaces.interactive_shell.command_registry import cli_parity as m

        captured: list[list[str]] = []

        def _fake_run_cli_command(_console: Console, args: list[str], **kwargs: object) -> bool:
            captured.append(args)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)

        session = Session()
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        dispatch_slash("/onboard", session, console)

        assert captured == [["onboard"]], "run_cli_command must be called with onboard args"

    @pytest.mark.parametrize(
        "slash_input",
        ["/guardrails", "/guardrails rules", "/guardrails --help"],
    )
    def test_slash_guardrails_opts_into_output_capture(
        self, monkeypatch: object, slash_input: str
    ) -> None:
        """Bare ``/guardrails`` (no subcommand), known subcommands, and flag-style
        invocations must all inherit the capturing default of
        ``run_cli_command`` (no ``capture_output=False`` opt-out). Without
        capture, Click's usage block (printed for the no-subcommand case) and
        subcommand output bypass ``console.print`` and never reach the REPL
        buffer — see issue #2388.
        """
        from surfaces.interactive_shell.command_registry import cli_parity as m

        captured_kwargs: list[dict[str, object]] = []

        def _fake_run_cli_command(_console: Console, _args: list[str], **kwargs: object) -> bool:
            captured_kwargs.append(kwargs)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)
        session = Session()
        dispatch_slash(slash_input, session, Console())

        assert captured_kwargs == [{"session": session}]

    def test_run_cli_command_captures_output_by_default(self) -> None:
        """The capturing default is the safety net the per-handler tests rely on:
        a printer that passes no ``capture_output`` must get capture, so its
        output reaches the REPL buffer and the agent's slash observation."""
        import inspect

        from surfaces.interactive_shell.command_registry.cli_parity import run_cli_command

        parameter = inspect.signature(run_cli_command).parameters["capture_output"]
        assert parameter.default is True

    @pytest.mark.parametrize(
        "slash_input",
        ["/cron", "/cron list", "/cron remove ecf7c2580b83"],
    )
    def test_slash_cron_printers_opt_into_output_capture(
        self, monkeypatch: object, slash_input: str
    ) -> None:
        """Non-daemon cron subcommands must capture output so the table (and
        task ids in it) reaches the REPL buffer and the slash history row."""
        from surfaces.interactive_shell.command_registry import cli_parity as m

        captured_kwargs: list[dict[str, object]] = []

        def _fake_run_cli_command(_console: Console, _args: list[str], **kwargs: object) -> bool:
            captured_kwargs.append(kwargs)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)
        session = Session()
        dispatch_slash(slash_input, session, Console())

        assert captured_kwargs == [{"capture_output": True, "session": session}]

    def test_slash_cron_start_streams_to_the_tty(self, monkeypatch: object) -> None:
        """The scheduler daemon blocks; capturing would buffer its output forever."""
        from surfaces.interactive_shell.command_registry import cli_parity as m

        captured_kwargs: list[dict[str, object]] = []

        def _fake_run_cli_command(_console: Console, _args: list[str], **kwargs: object) -> bool:
            captured_kwargs.append(kwargs)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)
        session = Session()
        dispatch_slash("/cron start", session, Console())

        assert captured_kwargs == [{"capture_output": False, "session": session}]

    def test_slash_onboard_with_args_forwards_them_to_subprocess(self, monkeypatch: object) -> None:
        """Args passed to ``/onboard`` must be forwarded to the subprocess."""
        from surfaces.interactive_shell.command_registry import cli_parity as m

        captured: list[list[str]] = []

        def _fake_run_cli_command(_console: Console, args: list[str], **kwargs: object) -> bool:
            captured.append(args)
            return True

        monkeypatch.setattr(m, "run_cli_command", _fake_run_cli_command)

        session = Session()
        buf = io.StringIO()
        console = Console(file=buf, force_terminal=False, width=200)
        dispatch_slash("/onboard local_llm", session, console)

        assert captured == [["onboard", "local_llm"]]


def test_alerts_inactive_prints_enable_instructions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The inactive warning must say how to turn the listener on."""
    # Arrange
    import surfaces.interactive_shell.command_registry.alerts as alerts_module

    monkeypatch.setattr(alerts_module, "get_current_inbox", lambda: None)
    console, buf = _capture()

    # Act
    dispatch_slash("/alerts", Session(), console)

    # Assert
    output = buf.getvalue()
    assert "not active" in output
    assert "alert_listener_enabled" in output
    assert "OPENSRE_ALERT_LISTENER_ENABLED" in output
