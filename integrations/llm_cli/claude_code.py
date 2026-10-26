"""Anthropic Claude Code CLI adapter (``claude -p``, non-interactive / print mode).

Env vars
--------
CLAUDE_CODE_BIN              Optional explicit path to the ``claude`` binary.
                             Blank or non-runnable paths are ignored; PATH + fallbacks apply.
CLAUDE_CODE_MODEL            Optional model override (e.g. ``claude-opus-4-7``).
                             Unset or empty → omit ``--model``; CLI default applies.
CLAUDE_CODE_TIMEOUT_SECONDS  Optional invocation timeout override in seconds for long prompts
                             (default: 120, min: 30, max: 600).

Auth
----
When the ``claude`` binary is available, OpenSRE probes ``claude auth status``
and treats Claude subscription login as first-class auth. ``ANTHROPIC_API_KEY``
and ``~/.claude/.credentials.json`` (under ``Path.home()`` on all platforms)
are used as fallbacks when the binary is unavailable.

Platforms
---------
Binary resolution uses ``shutil.which`` with ``claude.cmd`` / ``claude.exe`` /
``.bat`` / ``.ps1`` on Windows, plus npm / Volta / pnpm style fallback dirs
(see ``default_cli_fallback_paths``). Without the CLI binary, macOS Keychain
may still hold OAuth credentials, so auth is reported as unclear until the
binary runs; Linux and Windows without env or creds file → not authenticated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from integrations.llm_cli.base import CLIInvocation, CLIProbe
from integrations.llm_cli.binary_resolver import (
    candidate_binary_names as _candidate_binary_names,
)
from integrations.llm_cli.binary_resolver import (
    default_cli_fallback_paths as _default_cli_fallback_paths,
)
from integrations.llm_cli.binary_resolver import (
    resolve_cli_binary,
)
from integrations.llm_cli.constants import (
    DEFAULT_EXEC_TIMEOUT_SEC as _DEFAULT_EXEC_TIMEOUT_SEC,
)
from integrations.llm_cli.constants import (
    MAX_EXEC_TIMEOUT_SEC as _MAX_EXEC_TIMEOUT_SEC,
)
from integrations.llm_cli.constants import (
    MIN_EXEC_TIMEOUT_SEC as _MIN_EXEC_TIMEOUT_SEC,
)
from integrations.llm_cli.env_overrides import (
    ANTHROPIC_CLI_ENV_KEYS,
    nonempty_env_values,
)
from integrations.llm_cli.output import require_nonempty_output
from integrations.llm_cli.probe_utils import run_version_probe
from integrations.llm_cli.semver_utils import parse_semver_three_part
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env
from integrations.llm_cli.timeout_utils import resolve_timeout_from_env

# Claude Code's `--version` does config/cache init that can spike past Codex's 3s
# budget on cold starts or when another claude process holds shared state.
_PROBE_TIMEOUT_SEC = 8.0
_AUTH_HINT = "Run: claude auth login or set ANTHROPIC_API_KEY."


def _resolve_exec_timeout_seconds() -> float:
    return resolve_timeout_from_env(
        env_key="CLAUDE_CODE_TIMEOUT_SECONDS",
        default=_DEFAULT_EXEC_TIMEOUT_SEC,
        minimum=_MIN_EXEC_TIMEOUT_SEC,
        maximum=_MAX_EXEC_TIMEOUT_SEC,
    )


def _anthropic_env_overrides() -> dict[str, str]:
    """Build Claude subprocess auth/config overrides used by probe and invoke."""
    env: dict[str, str] = {"NO_COLOR": "1"}
    env.update(nonempty_env_values(ANTHROPIC_CLI_ENV_KEYS))
    return env


def _anthropic_auth_env_source() -> str | None:
    """Return the active Anthropic auth env key, if present."""
    env = _anthropic_env_overrides()
    if env.get("ANTHROPIC_API_KEY"):
        return "ANTHROPIC_API_KEY"
    if env.get("ANTHROPIC_AUTH_TOKEN"):
        return "ANTHROPIC_AUTH_TOKEN"
    return None


def _auth_status_from_json_payload(data: dict) -> tuple[bool, str]:
    """Map `claude auth status` JSON object to (logged_in, user-facing detail).

    The CLI emits ``apiKeySource`` whenever the env contributes an API key,
    even when the active auth method is the subscription. Use ``authMethod``
    (``claude.ai`` / ``api_key`` / ``none``) as the authoritative discriminator
    for the detail string; ``apiKeySource`` and ``email`` are supporting detail
    and are also used as a legacy fallback for older CLI versions that omit
    ``authMethod``.
    """
    if not data.get("loggedIn"):
        return False, f"Not authenticated. {_AUTH_HINT}"
    auth_method = str(data.get("authMethod") or "").lower()
    email = str(data.get("email") or "")
    api_key_source = str(data.get("apiKeySource") or "")

    if auth_method == "api_key":
        source = api_key_source or "ANTHROPIC_API_KEY"
        return True, f"Authenticated via {source}."
    if auth_method == "claude.ai":
        return True, f"Authenticated via Claude subscription{f' ({email})' if email else ''}."
    if auth_method:
        # Unrecognized but non-empty authMethod (e.g. a future "oauth" / "sso"):
        # surface it verbatim instead of leaning on apiKeySource, which the CLI
        # also populates for env-supplied API keys regardless of the active
        # method and would mis-report the source.
        return True, f"Authenticated via {auth_method}{f' ({email})' if email else ''}."
    # Older CLI versions may omit authMethod — fall back to legacy heuristic.
    if api_key_source:
        return True, f"Authenticated via {api_key_source}."
    if email:
        return True, f"Authenticated via Claude subscription ({email})."
    return True, "Authenticated via Claude CLI."


def _try_parse_auth_status_stdout(stdout: str) -> tuple[bool, str] | None:
    """If stdout is a JSON object from ``auth status``, return auth; else None."""
    raw = (stdout or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return _auth_status_from_json_payload(data)


def _probe_cli_auth(binary_path: str) -> tuple[bool | None, str]:
    """Check Claude Code auth via `claude auth status` (local, no API call).

    Returns ``(True, …)`` for any working auth (subscription or API key),
    ``(False, …)`` when the CLI definitively reports the user as not logged
    in, and ``(None, …)`` only when the probe itself failed (timeout, spawn
    error, or unparseable output from an older CLI).

    The CLI exits **non-zero** when ``loggedIn`` is false (CLI ≥ 2.x) while
    still printing valid JSON, so we parse stdout first and only fall back to
    treating a non-zero exit as an opaque probe failure when the JSON is
    absent or unparseable. The previous behaviour of returning ``None`` on
    any non-zero exit hid the real "not logged in" state behind the wizard's
    "could not verify" branch.
    """
    try:
        proc = subprocess.run(
            [binary_path, "auth", "status"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT_SEC,
            check=False,
            env=build_cli_subprocess_env(_anthropic_env_overrides()),
        )
    except subprocess.TimeoutExpired:
        return (
            None,
            f"claude auth status timed out after {_PROBE_TIMEOUT_SEC:.0f} s — auth state unknown.",
        )
    except OSError as exc:
        return None, f"Could not spawn claude for auth probe: {exc}"

    parsed = _try_parse_auth_status_stdout(proc.stdout)
    if parsed is not None:
        return parsed

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:500]
        return None, f"claude auth status failed: {err or 'unknown error'}"

    # Older CLI versions may not output JSON; classify explicit negative
    # phrases first to avoid false positives like "Not logged in" (exit 0).
    plain = (proc.stdout or proc.stderr or "").strip().lower()
    negative_markers = (
        "not logged in",
        "not authenticated",
        "login required",
        "unauthenticated",
    )
    if any(marker in plain for marker in negative_markers):
        return False, f"Not authenticated. {_AUTH_HINT}"
    return True, "Authenticated via Claude CLI."


def _classify_claude_code_auth(binary_path: str | None = None) -> tuple[bool | None, str]:
    """Return (logged_in, detail) for Claude Code auth.

    Resolution order:
    1. Binary available → `claude auth status` is the source of truth for all
       platforms; covers both subscription login and ANTHROPIC_API_KEY.
    2. No binary, ANTHROPIC_API_KEY set → True (filesystem-independent fallback).
    3. No binary, credentials file present → True (OAuth login).
    4. No binary, macOS → None (Keychain may hold credentials; invocation will verify).
    5. No binary, Linux/Windows → False.
    """
    if binary_path:
        return _probe_cli_auth(binary_path)
    auth_env_source = _anthropic_auth_env_source()
    if auth_env_source:
        return True, f"Authenticated via {auth_env_source}."
    creds_path = Path.home() / ".claude" / ".credentials.json"
    try:
        if creds_path.exists() and creds_path.stat().st_size > 2:
            return True, "Authenticated via ~/.claude/.credentials.json (OAuth login)."
    except OSError:
        return None, "Could not read ~/.claude/.credentials.json; auth state unclear."
    if sys.platform == "darwin":
        return None, (f"Auth state unclear — binary unavailable for verification. {_AUTH_HINT}")
    return (
        False,
        f"Not authenticated. {_AUTH_HINT}",
    )


def _fallback_claude_code_paths() -> list[str]:
    return _default_cli_fallback_paths("claude")


class ClaudeCodeAdapter:
    """Non-interactive Claude Code CLI (``claude -p``, print mode, no TTY)."""

    name = "claude-code"
    streams_plain_stdout = True
    binary_env_key = "CLAUDE_CODE_BIN"
    install_hint = "npm i -g @anthropic-ai/claude-code"
    auth_hint = _AUTH_HINT.removesuffix(".")
    min_version: str | None = None
    default_exec_timeout_sec = _DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="CLAUDE_CODE_BIN",
            binary_names=_candidate_binary_names("claude"),
            fallback_paths=_fallback_claude_code_paths,
        )

    def _probe_binary(self, binary_path: str) -> CLIProbe:
        version_output, version_error = run_version_probe(
            binary_path,
            timeout_sec=_PROBE_TIMEOUT_SEC,
        )
        if version_error:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=version_error,
            )

        version = parse_semver_three_part(version_output or "")
        logged_in, auth_detail = _classify_claude_code_auth(binary_path=binary_path)
        auth_env_source = _anthropic_auth_env_source()
        if logged_in is not True and auth_env_source:
            logged_in = True
            auth_detail = f"Authenticated via {auth_env_source} fallback."
        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary_path,
            detail=auth_detail,
        )

    def detect(self) -> CLIProbe:
        binary = self._resolve_binary()
        if not binary:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=(
                    "Claude Code CLI not found on PATH or known install locations. "
                    f"Install with: {self.install_hint} or set CLAUDE_CODE_BIN."
                ),
            )
        return self._probe_binary(binary)

    def build(
        self,
        *,
        prompt: str,
        model: str | None,
        workspace: str,
        reasoning_effort: str | None = None,
    ) -> CLIInvocation:
        _ = reasoning_effort
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                f"Claude Code CLI not found. {self.install_hint}"
                " or set CLAUDE_CODE_BIN to the full binary path."
            )

        ws = (workspace or "").strip()
        cwd = str(Path(ws).expanduser()) if ws else os.getcwd()

        argv: list[str] = [
            binary,
            "-p",
            "--output-format",
            "text",
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["--model", resolved_model])

        # Forward Anthropic auth vars explicitly rather than relying on a blanket
        # prefix allowlist, so they don't leak into other CLI adapters (e.g. Codex).
        env = _anthropic_env_overrides()

        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
            cwd=cwd,
            env=env,
            timeout_sec=_resolve_exec_timeout_seconds(),
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        return require_nonempty_output(stdout, stderr, returncode, self.explain_failure)

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        from integrations.llm_cli.failure_explain import explain_cli_failure

        return explain_cli_failure(
            exit_label="claude -p",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )
