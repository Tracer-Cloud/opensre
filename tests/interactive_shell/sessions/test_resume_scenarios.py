"""End-to-end /resume scenario tests: session identity, JSONL turns, slash recording."""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError
from rich.console import Console

from config.llm_auth.credentials import status as credential_status
from config.llm_settings import (
    get_configured_llm_provider,
    get_llm_provider_api_key_env,
    resolve_llm_settings_verbose,
)
from core.agent_harness.session import InMemorySessionStore, JsonlSessionRepo, JsonlSessionStore
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.session import Session

SessionState = JsonlSessionStore()


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False), buf


def _read_turns(path: Path) -> list[dict]:
    if not path.exists():
        return []
    turns: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("type") == "custom_message" and rec.get("custom_type") == "turn_stub":
            turns.append({"type": "turn", "kind": rec.get("kind"), "text": rec.get("text")})
    return turns


def _write_finalized_session(
    sessions_dir: Path,
    session_id: str,
    *,
    chat_text: str = "why is redis slow?",
    messages: list[tuple[str, str]] | None = None,
    context: dict[str, str] | None = None,
) -> Path:
    """Create a closed session file that /resume can load."""
    path = sessions_dir / f"{session_id}.jsonl"
    msgs = messages or [("user", chat_text), ("assistant", "check connection pool")]
    ctx = context or {"service": "redis"}
    ids = [f"entry{i}" for i in range(1, 6)]
    lines = [
        json.dumps(
            {
                "type": "session",
                "version": 2,
                "id": session_id,
                "created_at": "2026-05-29T10:00:00+00:00",
                "cwd": "",
            }
        ),
        json.dumps(
            {
                "id": ids[0],
                "parent_id": None,
                "timestamp": "2026-05-29T10:00:01+00:00",
                "type": "custom_message",
                "custom_type": "turn_stub",
                "kind": "chat",
                "text": chat_text,
                "display": False,
            }
        ),
        json.dumps(
            {
                "id": ids[1],
                "parent_id": ids[0],
                "timestamp": "2026-05-29T10:00:02+00:00",
                "type": "message",
                "role": "user",
                "content": msgs[0][1],
                "metadata": {"kind": "chat"},
            }
        ),
        json.dumps(
            {
                "id": ids[2],
                "parent_id": ids[1],
                "timestamp": "2026-05-29T10:00:03+00:00",
                "type": "message",
                "role": "assistant",
                "content": msgs[1][1],
                "metadata": {"kind": "chat"},
            }
        ),
        json.dumps(
            {
                "id": ids[3],
                "parent_id": ids[2],
                "timestamp": "2026-05-29T10:00:04+00:00",
                "type": "custom_message",
                "custom_type": "accumulated_context",
                "content": ctx,
                "display": False,
            }
        ),
        json.dumps(
            {
                "id": ids[4],
                "parent_id": ids[3],
                "timestamp": "2026-05-29T10:00:05+00:00",
                "type": "leaf",
                "total_turns": 1,
            }
        ),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def isolated_sessions(tmp_path: Path) -> Path:
    """Sessions directory with SessionState patched for the test."""
    directory = tmp_path / "sessions"
    directory.mkdir()
    with (
        patch("config.constants.OPENSRE_HOME_DIR", tmp_path),
        patch("config.constants.paths.OPENSRE_HOME_DIR", tmp_path),
    ):
        yield directory


def _open_current(session: Session) -> None:
    SessionState.open_session(session)


def _require_live_llm_for_repl_planner() -> None:
    explicit_pin = os.environ.get("LLM_PROVIDER", "").strip().lower()
    resolution = None
    try:
        resolution = resolve_llm_settings_verbose()
    except ValidationError as exc:
        provider = get_configured_llm_provider()
        env_var = get_llm_provider_api_key_env(provider)
        msg = exc.errors()[0].get("msg", str(exc)) if exc.errors() else str(exc)
        hint = f" configured provider={provider!r}"
        if env_var is not None:
            hint += f", required key={env_var}"
        pytest.skip(f"Skipping live REPL planner smoke; missing LLM configuration:{hint}. {msg}")

    if resolution is None:
        pytest.skip("Skipping live REPL planner smoke; LLM configuration was not resolved.")

    if explicit_pin and resolution.resolved_provider != explicit_pin:
        pytest.skip(
            f"Skipping live REPL planner smoke; LLM_PROVIDER={explicit_pin!r} resolved as "
            f"{resolution.resolved_provider!r}."
        )

    auth = credential_status(resolution.resolved_provider)
    if not auth.configured or auth.stale:
        pytest.skip(
            "Skipping live REPL planner smoke; missing LLM credentials:"
            f" provider={resolution.resolved_provider!r}, auth={auth.source}, detail={auth.detail}"
        )

    env_var = get_llm_provider_api_key_env(resolution.resolved_provider)
    if env_var is not None and not os.environ.get(env_var):
        pytest.skip(
            f"Skipping live REPL planner smoke; {env_var} is not exported to the child REPL."
        )


class TestResumeScenarioMatrix:
    """Scenario coverage for /resume session adoption and JSONL persistence."""

    @pytest.mark.parametrize("already_current", [False, True])
    def test_resume_distinguishes_names_with_different_spacing(
        self, isolated_sessions: Path, already_current: bool
    ) -> None:
        target_id = "aaaa1111-2222-3333-4444-555566667777"
        other_id = "bbbb2222-3333-4444-5555-666677778888"
        for session_id, name in ((target_id, "weekly review"), (other_id, "weekly  review")):
            _write_finalized_session(isolated_sessions, session_id, chat_text=name)
            SessionState.append_session_name(session_id, name)
        session = Session()
        _open_current(session)
        console, _ = _capture()
        if already_current:
            dispatch_slash(f"/resume {target_id}", session, console)
        console, output = _capture()

        dispatch_slash("/resume weekly review", session, console)

        assert session.session_id == target_id
        assert session.agent.messages[0] == ("user", "weekly review")
        if already_current:
            assert "current session" in output.getvalue()
            assert "↩ " not in output.getvalue()

    @pytest.mark.parametrize("current_name", ["weekly", "weekly planning"])
    def test_resume_name_search_ignores_current_session(
        self, isolated_sessions: Path, current_name: str
    ) -> None:
        target_id = "aaaa1111-2222-3333-4444-555566667777"
        _write_finalized_session(isolated_sessions, target_id, chat_text="weekly notes")
        SessionState.append_session_name(target_id, "weekly notes")
        session = Session()
        _open_current(session)
        SessionState.append_session_name(session.session_id, current_name)
        console, output = _capture()

        dispatch_slash("/resume weekly", session, console)

        assert session.session_id == target_id
        assert session.agent.messages[0] == ("user", "weekly notes")
        assert "↩ " in output.getvalue()

    def test_rename_persists_for_listing_and_resume_then_resets(
        self, isolated_sessions: Path
    ) -> None:
        target_id = "aaaa1111-2222-3333-4444-555566667777"
        path = _write_finalized_session(isolated_sessions, target_id)
        other_id = "bbbb2222-3333-4444-5555-666677778888"
        _write_finalized_session(isolated_sessions, other_id)
        SessionState.append_session_name(other_id, "weekly planning")
        session = Session()
        _open_current(session)
        console, _ = _capture()
        dispatch_slash(f"/resume {target_id[:8]}", session, console)
        messages = list(session.agent.messages)
        context = dict(session.accumulated_context)
        before = path.read_bytes()
        history_size = len(session.history)
        name = "[b]weekly  review[/b]"

        dispatch_slash(f"/rename '{name}'", session, console)

        assert len(session.history) == history_size + 1
        assert session.session_id == target_id
        assert session.agent.messages == messages
        assert session.accumulated_context == context
        assert path.read_bytes().startswith(before)
        saved = JsonlSessionRepo().load_session(target_id)
        assert saved is not None
        assert saved["name"] == name
        assert saved["cli_agent_messages"] == messages
        assert saved["accumulated_context"] == context
        assert sum(t["text"].startswith("/rename") for t in _read_turns(path)) == 1
        list_console, output = _capture()
        list_console.width = 120
        with patch(
            "surfaces.shared.terminal.components.rendering.shutil.get_terminal_size",
            return_value=os.terminal_size((120, 24)),
        ):
            dispatch_slash("/sessions", session, list_console)
        assert name in output.getvalue()

        resumed = Session()
        _open_current(resumed)
        with (
            patch(
                "surfaces.interactive_shell.command_registry.session_cmds.resume.repl_tty_interactive",
                return_value=True,
            ),
            patch(
                "surfaces.interactive_shell.command_registry.session_cmds.resume.choose_resume_session",
                return_value=None,
            ) as picker,
        ):
            dispatch_slash("/resume", resumed, console)
        assert picker.call_args.args[0][0].title == name
        dispatch_slash("/resume weekly review", resumed, console)
        assert resumed.session_id == target_id
        assert resumed.agent.messages == messages
        dispatch_slash("/rename --reset", resumed, console)
        saved = JsonlSessionRepo().load_session(target_id)
        assert saved is not None
        assert saved["name"] == "why is redis slow?"

    @pytest.mark.parametrize("opened", [True, False])
    def test_rename_records_one_turn_with_the_storage_outcome(self, opened: bool) -> None:
        storage = InMemorySessionStore()
        session = Session(store=storage)
        if opened:
            storage.open_session(session)
        console, output = _capture()

        dispatch_slash("/rename checkout", session, console)

        assert len(session.history) == 1
        assert session.history[0]["ok"] is opened
        if opened:
            record = storage.read(session.session_id)[-1]
            assert record["custom_type"] == "session_name"
            assert record["sidecar"] is True
            assert record["parent_id"] is None
        else:
            assert "could not save" in output.getvalue()

    def test_scenario_fresh_repl_resumes_prior_session(self, isolated_sessions: Path) -> None:
        """Fresh REPL session resumes a prior session: adopt ID, slash on target only."""
        target_id = "aaaa1111-2222-3333-4444-555566667777"
        _write_finalized_session(isolated_sessions, target_id, chat_text="why is redis slow?")

        session = Session()
        current_id = session.session_id
        _open_current(session)

        console, buf = _capture()
        assert dispatch_slash(f"/resume {target_id[:8]}", session, console) is True

        assert session.session_id == target_id
        assert "↩ " in buf.getvalue()

        current_path = isolated_sessions / f"{current_id}.jsonl"
        if current_path.exists():
            assert not any(
                t.get("kind") == "slash" and "/resume" in t.get("text", "")
                for t in _read_turns(current_path)
            )

        target_turns = _read_turns(isolated_sessions / f"{target_id}.jsonl")
        assert any(
            t.get("kind") == "slash" and t.get("text", "").startswith("/resume")
            for t in target_turns
        )
        assert session.agent.messages[0] == ("user", "why is redis slow?")
        assert session.accumulated_context == {"service": "redis"}

    def test_scenario_post_resume_slash_and_chat_append_to_target(
        self,
        isolated_sessions: Path,
    ) -> None:
        """After /resume, further slash commands and chat turns land on the same file."""
        target_id = "bbbb2222-3333-4444-5555-666677778888"
        _write_finalized_session(isolated_sessions, target_id)

        session = Session()
        _open_current(session)
        console, _ = _capture()

        dispatch_slash(f"/resume {target_id[:8]}", session, console)
        dispatch_slash("/status", session, console)
        session.record("chat", "follow-up question after resume")

        target_path = isolated_sessions / f"{target_id}.jsonl"
        kinds = [t["kind"] for t in _read_turns(target_path)]
        assert "slash" in kinds
        assert kinds.count("slash") >= 2
        assert "chat" in kinds
        assert "leaf" in target_path.read_text(encoding="utf-8")

    def test_scenario_empty_starter_session_removed_on_resume(
        self,
        isolated_sessions: Path,
    ) -> None:
        """A brand-new REPL with no turns should not leave a junk file after /resume."""
        target_id = "cccc3333-4444-5555-6666-777788889999"
        _write_finalized_session(isolated_sessions, target_id)

        session = Session()
        starter_id = session.session_id
        _open_current(session)
        console, _ = _capture()

        dispatch_slash(f"/resume {target_id[:8]}", session, console)

        starter_path = isolated_sessions / f"{starter_id}.jsonl"
        assert not starter_path.exists()
        assert session.session_id == target_id

    def test_scenario_resume_by_name_substring(self, isolated_sessions: Path) -> None:
        target_id = "dddd4444-5555-6666-7777-888899990000"
        _write_finalized_session(isolated_sessions, target_id, chat_text="investigate OOM killer")

        session = Session()
        _open_current(session)
        console, buf = _capture()

        dispatch_slash("/resume OOM", session, console)

        assert session.session_id == target_id
        assert "↩ " in buf.getvalue()
        target_turns = _read_turns(isolated_sessions / f"{target_id}.jsonl")
        assert any(t.get("text") == "/resume OOM" for t in target_turns)

    def test_resume_session_by_prefix_matches_name_substring(
        self,
        isolated_sessions: Path,
    ) -> None:
        from surfaces.interactive_shell.command_registry.session_cmds.resume import (
            resume_session_by_prefix,
        )

        target_id = "eeee5555-6666-7777-8888-999900001111"
        _write_finalized_session(isolated_sessions, target_id, chat_text="investigate OOM killer")

        session = Session()
        _open_current(session)
        console, buf = _capture()

        assert resume_session_by_prefix("OOM", session, console)
        assert session.session_id == target_id
        assert "↩ " in buf.getvalue()

    def test_scenario_resume_not_found_records_on_current(
        self,
        isolated_sessions: Path,
    ) -> None:
        session = Session()
        current_id = session.session_id
        _open_current(session)
        console, buf = _capture()

        dispatch_slash("/resume deadbeef", session, console)

        assert session.session_id == current_id
        assert "not found" in buf.getvalue()
        turns = _read_turns(isolated_sessions / f"{current_id}.jsonl")
        assert turns[-1]["kind"] == "slash"
        assert turns[-1]["text"] == "/resume deadbeef"
        assert session.history[-1]["ok"] is False

    def test_scenario_resume_current_session_guard(
        self,
        isolated_sessions: Path,
    ) -> None:
        session = Session()
        _open_current(session)
        session.record("chat", "still working here")
        console, buf = _capture()

        dispatch_slash(f"/resume {session.session_id[:8]}", session, console)

        assert "current session" in buf.getvalue()
        assert session.session_id  # unchanged
        turns = _read_turns(isolated_sessions / f"{session.session_id}.jsonl")
        assert any(t.get("text", "").startswith("/resume") for t in turns)

    def test_scenario_resume_empty_target_does_not_switch(
        self,
        isolated_sessions: Path,
    ) -> None:
        empty_id = "eeee5555-6666-7777-8888-999900001111"
        path = isolated_sessions / f"{empty_id}.jsonl"
        path.write_text(
            json.dumps(
                {
                    "type": "session",
                    "version": 2,
                    "id": empty_id,
                    "created_at": "2026-05-29T10:00:00+00:00",
                    "cwd": "",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        session = Session()
        current_id = session.session_id
        _open_current(session)
        console, buf = _capture()

        dispatch_slash(f"/resume {empty_id[:8]}", session, console)

        assert session.session_id == current_id
        assert "no conversation to resume" in buf.getvalue()

    def test_sessions_picker_resumes_a_command_only_session(
        self,
        isolated_sessions: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A listed session with prior slash activity can be continued with Enter."""
        from surfaces.interactive_shell.command_registry.session_cmds import list as list_command

        target_id = "abab5555-6666-7777-8888-999900001111"
        (isolated_sessions / f"{target_id}.jsonl").write_text(
            "\n".join(
                json.dumps(record)
                for record in (
                    {
                        "type": "session",
                        "version": 2,
                        "id": target_id,
                        "created_at": "2026-05-29T10:00:00+00:00",
                        "cwd": "",
                    },
                    {
                        "id": "entry1",
                        "parent_id": None,
                        "timestamp": "2026-05-29T10:00:01+00:00",
                        "type": "custom_message",
                        "custom_type": "turn_stub",
                        "kind": "slash",
                        "text": "/choose",
                    },
                )
            )
            + "\n",
            encoding="utf-8",
        )
        session = Session()
        current_id = session.session_id
        _open_current(session)
        console, buf = _capture()
        monkeypatch.setattr(list_command, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(list_command, "choose_recent_session", lambda _items: target_id)

        dispatch_slash("/sessions", session, console)

        assert session.session_id == target_id
        assert "↩ " in buf.getvalue()
        target_turns = _read_turns(isolated_sessions / f"{target_id}.jsonl")
        assert any(turn["text"] == f"/sessions {target_id[:8]}" for turn in target_turns)
        source_turns = _read_turns(isolated_sessions / f"{current_id}.jsonl")
        assert not any(turn["text"] == "/sessions" for turn in source_turns)

    def test_scenario_chain_resume_two_targets(
        self,
        isolated_sessions: Path,
    ) -> None:
        """Resume session A, then resume session B — each gets its own /resume slash turn."""
        id_a = "ffff6666-7777-8888-9999-000011112222"
        id_b = "11117777-8888-9999-0000-111122223333"
        _write_finalized_session(isolated_sessions, id_a, chat_text="session A question")
        _write_finalized_session(isolated_sessions, id_b, chat_text="session B question")

        session = Session()
        _open_current(session)
        console, _ = _capture()

        dispatch_slash(f"/resume {id_a[:8]}", session, console)
        assert session.session_id == id_a

        dispatch_slash(f"/resume {id_b[:8]}", session, console)
        assert session.session_id == id_b

        turns_a = _read_turns(isolated_sessions / f"{id_a}.jsonl")
        turns_b = _read_turns(isolated_sessions / f"{id_b}.jsonl")
        assert any("/resume" in t.get("text", "") for t in turns_a)
        assert any("/resume" in t.get("text", "") for t in turns_b)
        assert session.agent.messages[0] == ("user", "session B question")

    def test_scenario_resume_interrupted_session_stashes_recovery_note(
        self,
        isolated_sessions: Path,
    ) -> None:
        """A session with a dangling WAL intent resumes with a recovery note."""
        target_id = "33339999-aaaa-bbbb-cccc-ddddeeeeffff"
        path = _write_finalized_session(isolated_sessions, target_id, chat_text="run 5 steps")
        wal_lines = [
            json.dumps(
                {
                    "id": "wal-i1",
                    "parent_id": None,
                    "timestamp": "2026-05-29T10:00:06+00:00",
                    "type": "tool_intent",
                    "sidecar": True,
                    "tool": "shell_run",
                    "arguments": {"command": "step-1 >> /tmp/demo_state.json"},
                    "tool_call_id": "call_1",
                    "seq": 1,
                }
            ),
            json.dumps(
                {
                    "id": "wal-c1",
                    "parent_id": None,
                    "timestamp": "2026-05-29T10:00:07+00:00",
                    "type": "tool_call",
                    "sidecar": True,
                    "tool": "shell_run",
                    "tool_call_id": "call_1",
                    "source": "wal",
                }
            ),
            # Interrupted: step 2's intent never committed.
            json.dumps(
                {
                    "id": "wal-i2",
                    "parent_id": None,
                    "timestamp": "2026-05-29T10:00:08+00:00",
                    "type": "tool_intent",
                    "sidecar": True,
                    "tool": "shell_run",
                    "arguments": {"command": "step-2 >> /tmp/demo_state.json"},
                    "tool_call_id": "call_2",
                    "seq": 2,
                    "user_text": "run 5 steps",
                }
            ),
        ]
        path.write_text(
            path.read_text(encoding="utf-8") + "\n".join(wal_lines) + "\n",
            encoding="utf-8",
        )

        session = Session()
        _open_current(session)
        console, buf = _capture()

        dispatch_slash(f"/resume {target_id[:8]}", session, console)

        assert session.session_id == target_id
        output = buf.getvalue()
        assert "↩ " in output
        assert "interrupted mid-turn" in output
        note = session.pending_recovery_note
        assert note is not None
        assert "shell_run step-2 >> /tmp/demo_state.json (step 2)" in note
        # The committed step must not appear as interrupted.
        assert "step-1" not in note

    def test_scenario_resume_clean_session_has_no_recovery_note(
        self,
        isolated_sessions: Path,
    ) -> None:
        target_id = "4444aaaa-bbbb-cccc-dddd-eeeeffff0000"
        _write_finalized_session(isolated_sessions, target_id)

        session = Session()
        _open_current(session)
        console, buf = _capture()

        dispatch_slash(f"/resume {target_id[:8]}", session, console)

        assert session.session_id == target_id
        assert "interrupted mid-turn" not in buf.getvalue()
        assert session.pending_recovery_note is None

    def test_scenario_active_session_with_turns_flushed_without_resume_slash(
        self,
        isolated_sessions: Path,
    ) -> None:
        """Switching away from a session that had real turns preserves them without /resume."""
        target_id = "22228888-9999-0000-1111-222233334444"
        _write_finalized_session(isolated_sessions, target_id)

        session = Session()
        current_id = session.session_id
        _open_current(session)
        session.record("chat", "work in progress")
        console, _ = _capture()

        dispatch_slash(f"/resume {target_id[:8]}", session, console)

        old_turns = _read_turns(isolated_sessions / f"{current_id}.jsonl")
        assert any(t["kind"] == "chat" for t in old_turns)
        assert not any(t.get("kind") == "slash" for t in old_turns)
        assert (isolated_sessions / f"{current_id}.jsonl").read_text(encoding="utf-8").find(
            '"type": "leaf"'
        ) != -1


@pytest.mark.integration
@pytest.mark.live_llm
class TestResumeLiveRepl:
    """Live REPL smoke test via ReplDriver with isolated HOME and real planner."""

    @pytest.mark.skip(
        reason="Flaky live-LLM REPL round-trip (subprocess + 60s waits); resume load "
        "logic is covered offline. Run manually via ReplDriver."
    )
    def test_live_resume_round_trip(self, tmp_path: Path) -> None:
        _require_live_llm_for_repl_planner()

        from tests.utils.repl_driver import ReplDriver

        home = tmp_path / "home"
        home.mkdir()
        sessions_dir = home / ".opensre" / "sessions"
        sessions_dir.mkdir(parents=True)
        target_id = "live9999-aaaa-bbbb-cccc-ddddeeeeffff"
        _write_finalized_session(sessions_dir, target_id, chat_text="live redis investigation")

        with ReplDriver(home=home, startup_wait=10.0) as repl:
            repl.send("/sessions", wait=1.0)
            assert repl.wait_until_contains("live9999", "live redis", timeout=60.0)

            repl.reset_output()
            repl.send(f"/resume {target_id[:8]}", wait=1.0)
            assert repl.wait_until_contains("↩ ", timeout=60.0)
            assert repl.wait_until_contains("live redis investigation", timeout=10.0)

            repl.reset_output()
            repl.send("/status", wait=1.0)
            assert repl.wait_until_contains("interactions", timeout=60.0)

        target_path = sessions_dir / f"{target_id}.jsonl"
        assert target_path.exists()
        turns = _read_turns(target_path)
        assert any(t.get("kind") == "slash" and "/resume" in t.get("text", "") for t in turns)
        assert any(t.get("kind") == "slash" and "/status" in t.get("text", "") for t in turns)
