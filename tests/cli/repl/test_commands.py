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

    def test_list_models_handles_missing_env_gracefully(
        self, monkeypatch: object
    ) -> None:
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

    def test_list_empty_integrations_prints_onboarding_hint(
        self, monkeypatch: object
    ) -> None:
        from app.cli.repl import commands as cmd_module

        monkeypatch.setattr(  # type: ignore[attr-defined]
            cmd_module,
            "_load_verified_integrations",
            list,  # callable returning []
        )
        console, buf = _capture()
        dispatch_slash("/list integrations", ReplSession(), console)
        assert "opensre onboard" in buf.getvalue()
