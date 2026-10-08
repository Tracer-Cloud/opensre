"""Session lifecycle slash commands: /clear, /new, /sessions, /resume, /compact, /goal."""

from __future__ import annotations

from surfaces.interactive_shell.command_registry.session_cmds.goal import _cmd_goal
from surfaces.interactive_shell.command_registry.session_cmds.lifecycle import (
    _cmd_clear,
    _cmd_compact,
    _cmd_new,
    _cmd_rename,
    _validate_rename_args,
)
from surfaces.interactive_shell.command_registry.session_cmds.list import (
    _cmd_sessions,
    _validate_sessions_args,
)
from surfaces.interactive_shell.command_registry.session_cmds.resume import (
    _cmd_resume,
)
from surfaces.interactive_shell.command_registry.types import SlashCommand

COMMANDS: list[SlashCommand] = [
    SlashCommand("/clear", "Clear the screen and re-render the banner.", _cmd_clear),
    SlashCommand(
        "/sessions",
        "Browse recent sessions and resume one with Enter.",
        _cmd_sessions,
        usage=("/sessions",),
        validate_args=_validate_sessions_args,
    ),
    SlashCommand(
        "/resume",
        "Resume a previous session by restoring its conversation context.",
        _cmd_resume,
        usage=("/resume <session-id-prefix>", "/resume <session-id-prefix>:<entry-id-prefix>"),
        notes=(
            "Restores cli_agent_messages and accumulated infra context from the chosen session.",
            "Bare /resume opens an interactive session picker in a TTY.",
            "Accepts a session ID prefix, entry ref, or name substring (e.g. /resume redis).",
            "Replaces the current session's LLM conversation context; warns if messages exist.",
        ),
    ),
    SlashCommand(
        "/rename",
        "Rename the current session.",
        _cmd_rename,
        usage=("/rename <name>", "/rename --reset"),
        validate_args=_validate_rename_args,
        first_arg_completions=(("--reset", "restore the automatically generated title"),),
    ),
    SlashCommand(
        "/new",
        "Start a new session while keeping the current conversation context.",
        _cmd_new,
        notes=(
            "Unlike /clear, /new rotates the session ID and resets state while keeping LLM context.",
            "Use after /resume to continue a conversation in a clean session file.",
        ),
    ),
    SlashCommand(
        "/compact",
        "Compact the current session context into a replayable summary entry.",
        _cmd_compact,
        usage=("/compact",),
        notes=(
            "Writes a compaction entry and keeps the most recent messages plus a summary.",
            "Useful before continuing a long-running session in the same REPL.",
        ),
    ),
    SlashCommand(
        "/goal",
        "Show, set, pause, resume, edit, or clear the multi-step goal.",
        _cmd_goal,
        usage=(
            "/goal",
            "/goal show",
            "/goal set [--max-turns N] <condition>",
            "/goal pause",
            "/goal resume",
            "/goal edit <condition>",
            "/goal clear",
        ),
        notes=(
            "Sugar over chat_until_goal — distinct from /work durable todos.",
            "Setting a goal queues the condition as the next turn (autosubmit).",
            "Pause keeps state without continuation; resume queues the condition again.",
        ),
        first_arg_completions=(
            ("show", "show the goal (duration, turns, tokens)"),
            ("set", "set a goal and start working on it immediately"),
            ("pause", "pause continuation (keep state)"),
            ("resume", "resume a paused goal"),
            ("edit", "change the condition in place"),
            ("clear", "clear the goal"),
        ),
    ),
]
