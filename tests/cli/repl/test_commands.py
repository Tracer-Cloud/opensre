"""Tests for slash command dispatch."""

from __future__ import annotations

import io

from rich.console import Console

from app.cli.repl.commands import SLASH_COMMANDS, dispatch_slash
from app.cli.repl.session import ReplSession


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


class TestDispatchSlash:
    def test_exit_returns_false(self) -> None:
        session = ReplSession()
        console, _ = _capture()
        assert dispatch_slash("/exit", session, console) is False
        assert dispatch_slash("/quit", session, console) is False

    def test_help_lists_all_commands(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/help", session, console) is True
        output = buf.getvalue()
        for name in SLASH_COMMANDS:
            assert name in output

    def test_question_mark_shortcut_runs_help(self) -> None:
        """`/?` is the canonical shortcut for `/help` (vim / less convention)."""
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/?", session, console) is True
        output = buf.getvalue()
        # Any slash command name suffices as proof the help table rendered.
        assert "/help" in output
        assert "/list" in output

    def test_trust_toggle(self) -> None:
        session = ReplSession()
        console, _ = _capture()
        assert session.trust_mode is False
        dispatch_slash("/trust", session, console)
        assert session.trust_mode is True
        dispatch_slash("/trust off", session, console)
        assert session.trust_mode is False

    def test_reset_clears_session(self) -> None:
        session = ReplSession()
        session.record("alert", "test")
        session.last_state = {"x": 1}
        session.trust_mode = True
        console, _ = _capture()

        dispatch_slash("/reset", session, console)

        assert session.history == []
        assert session.last_state is None
        assert session.trust_mode is True  # reset keeps trust mode

    def test_status_shows_session_fields(self) -> None:
        session = ReplSession()
        session.record("alert", "hello")
        console, buf = _capture()
        dispatch_slash("/status", session, console)
        output = buf.getvalue()
        assert "interactions" in output
        assert "trust mode" in output

    def test_unknown_command_does_not_exit(self) -> None:
        session = ReplSession()
        console, buf = _capture()
        assert dispatch_slash("/made-up", session, console) is True
        assert "unknown command" in buf.getvalue()

    def test_empty_input_is_noop(self) -> None:
        session = ReplSession()
        console, _ = _capture()
        assert dispatch_slash("   ", session, console) is True


class TestListCommand:
    """Coverage for /list integrations / models / mcp and the default summary."""

    _FAKE_INTEGRATIONS = [
        {"service": "datadog", "source": "store", "status": "ok", "detail": "API ok"},
        {"service": "slack", "source": "env", "status": "missing", "detail": "No bot token"},
        {"service": "github", "source": "store", "status": "ok", "detail": "MCP ok"},
        {"service": "openclaw", "source": "store", "status": "failed", "detail": "401 from server"},
    ]

    def _patch_verify(self, monkeypatch: object) -> None:
        # Import inside test to match the lazy-import used by the handler.
        from app.cli.repl import commands as cmd_module

        monkeypatch.setattr(  # type: ignore[attr-defined]
            cmd_module,
            "_load_verified_integrations",
            lambda: list(self._FAKE_INTEGRATIONS),
        )

    def test_list_integrations_excludes_mcp_services(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list integrations", ReplSession(), console)
        output = buf.getvalue()
        assert "datadog" in output
        assert "slack" in output
        # MCP-classified services are reserved for /list mcp.
        assert "openclaw" not in output
        assert "github" not in output

    def test_list_mcp_shows_only_mcp_services(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list mcp", ReplSession(), console)
        output = buf.getvalue()
        assert "openclaw" in output
        assert "github" in output
        assert "datadog" not in output

    def test_list_mcps_alias(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list mcps", ReplSession(), console)
        assert "openclaw" in buf.getvalue()

    def _patch_llm(self, monkeypatch: object) -> None:
        """Provide a stable fake LLMSettings so the test doesn't depend on env."""
        from app.cli.repl import commands as cmd_module

        class _FakeLLM:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4"
            anthropic_toolcall_model = "claude-haiku-4"

        monkeypatch.setattr(  # type: ignore[attr-defined]
            cmd_module, "_load_llm_settings", lambda: _FakeLLM()
        )

    def test_list_models_shows_provider_and_models(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list models", ReplSession(), console)
        output = buf.getvalue()
        assert "provider" in output
        assert "reasoning model" in output
        assert "toolcall model" in output
        assert "anthropic" in output

    def test_list_models_handles_missing_env_gracefully(self, monkeypatch: object) -> None:
        from app.cli.repl import commands as cmd_module

        monkeypatch.setattr(  # type: ignore[attr-defined]
            cmd_module, "_load_llm_settings", lambda: None
        )
        console, buf = _capture()
        dispatch_slash("/list models", ReplSession(), console)
        assert "LLM settings unavailable" in buf.getvalue()

    def test_list_default_shows_all_three_sections(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list", ReplSession(), console)
        output = buf.getvalue()
        assert "Integrations" in output
        assert "MCP servers" in output
        assert "LLM connection" in output

    def test_list_unknown_target_prints_hint(self, monkeypatch: object) -> None:
        self._patch_verify(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/list bogus", ReplSession(), console)
        output = buf.getvalue()
        assert "unknown list target" in output
        assert "/list integrations" in output

    def test_list_empty_integrations_prints_onboarding_hint(self, monkeypatch: object) -> None:
        from app.cli.repl import commands as cmd_module

        monkeypatch.setattr(  # type: ignore[attr-defined]
            cmd_module,
            "_load_verified_integrations",
            list,  # callable returning []
        )
        console, buf = _capture()
        dispatch_slash("/list integrations", ReplSession(), console)
        assert "opensre onboard" in buf.getvalue()


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
        from app.cli.repl import commands as m

        monkeypatch.setattr(m, "_load_verified_integrations", lambda: list(self._FAKE))  # type: ignore[attr-defined]

    def test_list_shows_non_mcp_services(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations list", ReplSession(), console)
        assert "datadog" in buf.getvalue()
        assert "github" not in buf.getvalue()

    def test_list_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations", ReplSession(), console)
        assert "datadog" in buf.getvalue()

    def test_verify_reports_issues(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations verify", ReplSession(), console)
        assert "need attention" in buf.getvalue()

    def test_verify_all_ok(self, monkeypatch: object) -> None:
        from app.cli.repl import commands as m

        monkeypatch.setattr(
            m,
            "_load_verified_integrations",
            lambda: [  # type: ignore[attr-defined]
                {"service": "datadog", "source": "env", "status": "ok", "detail": "ok"},
            ],
        )
        console, buf = _capture()
        dispatch_slash("/integrations verify", ReplSession(), console)
        assert "all integrations ok" in buf.getvalue()

    def test_show_known_service(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations show datadog", ReplSession(), console)
        assert "datadog" in buf.getvalue()

    def test_show_unknown_service(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations show bogus", ReplSession(), console)
        assert "service not found" in buf.getvalue()

    def test_show_missing_arg(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations show", ReplSession(), console)
        assert "usage" in buf.getvalue()

    def test_unknown_subcommand_prints_hint(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/integrations bogus", ReplSession(), console)
        assert "unknown subcommand" in buf.getvalue()


class TestMcpCommand:
    _FAKE = [
        {"service": "github", "source": "store", "status": "ok", "detail": "MCP ok"},
        {"service": "openclaw", "source": "store", "status": "ok", "detail": "ok"},
    ]

    def _patch(self, monkeypatch: object) -> None:
        from app.cli.repl import commands as m

        monkeypatch.setattr(m, "_load_verified_integrations", lambda: list(self._FAKE))  # type: ignore[attr-defined]

    def test_list_shows_mcp_services(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp list", ReplSession(), console)
        assert "github" in buf.getvalue()

    def test_list_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp", ReplSession(), console)
        assert "github" in buf.getvalue()

    def test_connect_prints_hint(self) -> None:
        console, buf = _capture()
        dispatch_slash("/mcp connect", ReplSession(), console)
        assert "integrations setup" in buf.getvalue()

    def test_disconnect_prints_hint(self) -> None:
        console, buf = _capture()
        dispatch_slash("/mcp disconnect", ReplSession(), console)
        assert "integrations remove" in buf.getvalue()

    def test_unknown_subcommand(self, monkeypatch: object) -> None:
        self._patch(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/mcp bogus", ReplSession(), console)
        assert "unknown subcommand" in buf.getvalue()


class TestModelCommand:
    def _patch_llm(self, monkeypatch: object) -> None:
        from app.cli.repl import commands as m

        class _Fake:
            provider = "anthropic"
            anthropic_reasoning_model = "claude-opus-4"
            anthropic_toolcall_model = "claude-haiku-4"

        monkeypatch.setattr(m, "_load_llm_settings", lambda: _Fake())  # type: ignore[attr-defined]

    def test_show_displays_model_info(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model show", ReplSession(), console)
        assert "anthropic" in buf.getvalue()

    def test_show_is_default_when_no_subcommand(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model", ReplSession(), console)
        assert "anthropic" in buf.getvalue()

    def test_set_prints_not_supported_message(self) -> None:
        console, buf = _capture()
        dispatch_slash("/model set claude-haiku", ReplSession(), console)
        assert "not yet supported" in buf.getvalue()

    def test_unknown_subcommand(self, monkeypatch: object) -> None:
        self._patch_llm(monkeypatch)
        console, buf = _capture()
        dispatch_slash("/model bogus", ReplSession(), console)
        assert "unknown subcommand" in buf.getvalue()


class TestVersionCommand:
    def test_shows_version_info(self) -> None:
        console, buf = _capture()
        dispatch_slash("/version", ReplSession(), console)
        output = buf.getvalue()
        assert "opensre" in output
        assert "python" in output
        assert "os" in output


class TestTemplateCommand:
    def test_known_template_prints_json(self) -> None:
        console, buf = _capture()
        dispatch_slash("/template generic", ReplSession(), console)
        assert "alert_name" in buf.getvalue()

    def test_unknown_template_prints_hint(self) -> None:
        console, buf = _capture()
        dispatch_slash("/template bogus", ReplSession(), console)
        assert "unknown template" in buf.getvalue()

    def test_missing_arg_prints_usage(self) -> None:
        console, buf = _capture()
        dispatch_slash("/template", ReplSession(), console)
        assert "usage" in buf.getvalue()


class TestInvestigateFileCommand:
    def test_missing_arg_prints_usage(self) -> None:
        console, buf = _capture()
        dispatch_slash("/investigate", ReplSession(), console)
        assert "usage" in buf.getvalue()

    def test_missing_file_prints_error(self) -> None:
        console, buf = _capture()
        dispatch_slash("/investigate /nonexistent/path.json", ReplSession(), console)
        assert "file not found" in buf.getvalue()

    def test_valid_file_runs_investigation(self, tmp_path: object, monkeypatch: object) -> None:
        import app.cli.investigate as inv_module

        alert_file = tmp_path / "alert.json"  # type: ignore[operator]
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")  # type: ignore[union-attr]

        captured: list[str] = []

        def _fake(alert_text: str, context_overrides: object = None) -> dict:
            captured.append(alert_text)
            return {"root_cause": "test cause"}

        monkeypatch.setattr(inv_module, "run_investigation_for_session", _fake)  # type: ignore[attr-defined]
        session = ReplSession()
        console, _ = _capture()
        dispatch_slash(f"/investigate {alert_file}", session, console)
        assert session.last_state == {"root_cause": "test cause"}
        assert '{"alert_name": "test"}' in captured[0]

    def test_investigate_accumulates_infra_context(
        self, tmp_path: object, monkeypatch: object
    ) -> None:
        """Regression for Greptile P1 (PR #591): /investigate previously skipped
        the context-accumulation step that `loop._run_new_alert` does after a
        free-text investigation, so subsequent follow-up alerts lost the infra
        hints (service / cluster / region) that /investigate just discovered."""
        import app.cli.investigate as inv_module

        alert_file = tmp_path / "alert.json"  # type: ignore[operator]
        alert_file.write_text('{"alert_name": "test"}', encoding="utf-8")  # type: ignore[union-attr]

        def _fake(alert_text: str, context_overrides: object = None) -> dict:
            return {
                "root_cause": "disk full",
                "service": "orders-api",
                "cluster_name": "prod-us-east",
                "region": "us-east-1",
            }

        monkeypatch.setattr(inv_module, "run_investigation_for_session", _fake)  # type: ignore[attr-defined]

        session = ReplSession()
        console, _ = _capture()
        dispatch_slash(f"/investigate {alert_file}", session, console)

        # The next free-text alert must inherit these — proving accumulation ran.
        assert session.accumulated_context == {
            "service": "orders-api",
            "cluster_name": "prod-us-east",
            "region": "us-east-1",
        }


# ---------------------------------------------------------------------------
# Task 4 — Session-state commands
# ---------------------------------------------------------------------------


class TestHistoryCommand:
    def test_empty_history_says_so(self) -> None:
        console, buf = _capture()
        dispatch_slash("/history", ReplSession(), console)
        assert "no history" in buf.getvalue()

    def test_history_shows_entries(self) -> None:
        session = ReplSession()
        session.record("alert", "pod crash in prod")
        session.record("slash", "/status")
        console, buf = _capture()
        dispatch_slash("/history", session, console)
        output = buf.getvalue()
        assert "alert" in output
        assert "slash" in output
        assert "pod crash in prod" in output

    def test_failed_entry_shows_x_marker(self) -> None:
        session = ReplSession()
        session.record("alert", "bad input", ok=False)
        console, buf = _capture()
        dispatch_slash("/history", session, console)
        assert "✗" in buf.getvalue()


class TestLastCommand:
    def test_no_investigation_says_so(self) -> None:
        console, buf = _capture()
        dispatch_slash("/last", ReplSession(), console)
        assert "no investigation" in buf.getvalue()

    def test_shows_root_cause(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "OOMKilled in orders-api"}
        console, buf = _capture()
        dispatch_slash("/last", session, console)
        assert "OOMKilled in orders-api" in buf.getvalue()

    def test_shows_problem_md_when_no_root_cause(self) -> None:
        session = ReplSession()
        session.last_state = {"problem_md": "## Summary\n\nlatency spike"}
        console, buf = _capture()
        dispatch_slash("/last", session, console)
        assert "latency spike" in buf.getvalue()

    def test_empty_state_says_no_content(self) -> None:
        session = ReplSession()
        session.last_state = {}
        console, buf = _capture()
        dispatch_slash("/last", session, console)
        assert "no report content" in buf.getvalue()


class TestSaveCommand:
    def test_no_investigation_says_so(self) -> None:
        console, buf = _capture()
        dispatch_slash("/save out.md", ReplSession(), console)
        assert "nothing to save" in buf.getvalue()

    def test_missing_arg_prints_usage(self) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "x"}
        console, buf = _capture()
        dispatch_slash("/save", session, console)
        assert "usage" in buf.getvalue()

    def test_saves_markdown(self, tmp_path: object) -> None:
        session = ReplSession()
        session.last_state = {"root_cause": "db timeout", "problem_md": "## Details\n\nlatency"}
        dest = tmp_path / "report.md"  # type: ignore[operator]
        console, buf = _capture()
        dispatch_slash(f"/save {dest}", session, console)
        assert "saved" in buf.getvalue()
        content = dest.read_text()  # type: ignore[union-attr]
        assert "db timeout" in content

    def test_saves_json(self, tmp_path: object) -> None:
        import json

        session = ReplSession()
        session.last_state = {"root_cause": "db timeout"}
        dest = tmp_path / "report.json"  # type: ignore[operator]
        console, _ = _capture()
        dispatch_slash(f"/save {dest}", session, console)
        data = json.loads(dest.read_text())  # type: ignore[union-attr]
        assert data["root_cause"] == "db timeout"


class TestContextCommand:
    def test_empty_context_says_so(self) -> None:
        console, buf = _capture()
        dispatch_slash("/context", ReplSession(), console)
        assert "no infra context" in buf.getvalue()

    def test_shows_accumulated_keys(self) -> None:
        session = ReplSession()
        session.accumulated_context = {"service": "orders-api", "region": "us-east-1"}
        console, buf = _capture()
        dispatch_slash("/context", session, console)
        output = buf.getvalue()
        assert "orders-api" in output
        assert "us-east-1" in output


class TestCostCommand:
    def test_no_token_data_shows_placeholder(self) -> None:
        console, buf = _capture()
        dispatch_slash("/cost", ReplSession(), console)
        assert "not available" in buf.getvalue()

    def test_shows_token_counts_when_available(self) -> None:
        session = ReplSession()
        session.token_usage = {"input": 1000, "output": 500}
        console, buf = _capture()
        dispatch_slash("/cost", session, console)
        output = buf.getvalue()
        assert "1,000" in output
        assert "500" in output


class TestVerboseCommand:
    def test_on_sets_env_var(self, monkeypatch: object) -> None:
        import os

        monkeypatch.delenv("TRACER_VERBOSE", raising=False)  # type: ignore[attr-defined]
        console, buf = _capture()
        dispatch_slash("/verbose on", ReplSession(), console)
        assert os.environ.get("TRACER_VERBOSE") == "1"
        assert "verbose logging on" in buf.getvalue()

    def test_off_removes_env_var(self, monkeypatch: object) -> None:
        import os

        monkeypatch.setenv("TRACER_VERBOSE", "1")  # type: ignore[attr-defined]
        console, buf = _capture()
        dispatch_slash("/verbose off", ReplSession(), console)
        assert "TRACER_VERBOSE" not in os.environ
        assert "verbose logging off" in buf.getvalue()

    def test_no_arg_turns_on(self, monkeypatch: object) -> None:
        import os

        monkeypatch.delenv("TRACER_VERBOSE", raising=False)  # type: ignore[attr-defined]
        console, _ = _capture()
        dispatch_slash("/verbose", ReplSession(), console)
        assert os.environ.get("TRACER_VERBOSE") == "1"


class TestCompactCommand:
    def test_nothing_to_compact_when_small(self) -> None:
        session = ReplSession()
        for i in range(5):
            session.record("slash", f"/cmd{i}")
        console, buf = _capture()
        dispatch_slash("/compact", session, console)
        assert "nothing to compact" in buf.getvalue()
        assert len(session.history) == 5

    def test_trims_to_20_when_over_limit(self) -> None:
        session = ReplSession()
        for i in range(30):
            session.record("slash", f"/cmd{i}")
        console, buf = _capture()
        dispatch_slash("/compact", session, console)
        assert len(session.history) == 20
        assert "compacted" in buf.getvalue()


class TestStopCommand:
    def test_prints_ctrl_c_hint(self) -> None:
        console, buf = _capture()
        dispatch_slash("/stop", ReplSession(), console)
        assert "Ctrl+C" in buf.getvalue()

    def test_cancel_alias_same_as_stop(self) -> None:
        console, buf = _capture()
        dispatch_slash("/cancel", ReplSession(), console)
        assert "Ctrl+C" in buf.getvalue()
