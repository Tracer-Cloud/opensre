"""GitHub Copilot CLI adapter (``copilot -p``, non-interactive / programmatic mode).

Env vars
--------
COPILOT_BIN     Optional explicit path to the ``copilot`` binary.
                Blank or non-runnable paths are ignored; PATH + fallbacks apply.
COPILOT_MODEL   Optional model override. Unset or empty → omit ``--model``;
                the CLI default applies.
COPILOT_HOME    Optional config directory override. Defaults to ``~/.copilot``.

Auth probe
----------
Copilot CLI does **not** expose a non-interactive auth-status subcommand.
``copilot login`` opens an OAuth device flow; ``/login`` / ``/logout`` are
slash commands that only work inside an interactive session.

We classify auth in this order (cheap probes only — no Copilot network call):

1. ``COPILOT_GITHUB_TOKEN`` / ``GH_TOKEN`` / ``GITHUB_TOKEN`` env var set
   → ``True``. These are the documented headless/CI auth fallbacks that the
   CLI checks before anything else.
2. ``gh auth status`` (when ``gh`` is on PATH) → parse stdout/stderr using
   strings that match current ``gh`` output (for example ``✓ Logged in to
   github.com account …``, ``- Active account: true``, or a ``- Token:`` line
   whose prefix is a Copilot-supported token type per GitHub docs: ``gho_``,
   ``github_pat_``, ``ghu_`` — **not** ``ghp_``). If ``COPILOT_GH_HOST`` or
   ``GH_HOST`` targets a non-default host, we run ``gh auth status --hostname …``
   as documented for GitHub Enterprise / data residency. Clearly logged-out
   phrasing → ``False``; spawn error / timeout / ambiguous → ``None``.
   Plaintext ``config.json`` under ``$COPILOT_HOME`` is **not** read: it is easy
   to mis-classify and keychain-backed logins omit it anyway.
   This matches Copilot's documented **GitHub CLI fallback**. **BYOK /
   ``COPILOT_OFFLINE``**: no GitHub token may be required; probe may still return
   ``None`` while ``copilot -p`` works — invoke-time failure is the real check.
3. Otherwise → ``None``. Auth state cannot be verified from env + ``gh``.
   The runner appends the auth hint on a non-zero exit; the wizard offers retry
   / repick.

Note: OS-level credential stores (macOS Keychain, Windows Credential
Manager, Linux libsecret) are intentionally **not** probed. Service-name
lookups are fragile across CLI versions and ``gh auth status`` already covers
the main interactive-login path more reliably on every platform.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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
from integrations.llm_cli.constants import DEFAULT_EXEC_TIMEOUT_SEC
from integrations.llm_cli.env_overrides import (
    COPILOT_CLI_CONFIG_ENV_KEYS,
    COPILOT_CLI_ENV_KEYS,
    nonempty_env_values,
)
from integrations.llm_cli.output import require_nonempty_output
from integrations.llm_cli.probe_utils import run_version_probe
from integrations.llm_cli.semver_utils import parse_semver_three_part

_PROBE_TIMEOUT_SEC = 5.0
_GH_AUTH_TIMEOUT_SEC = 5.0

# ``gh auth status`` prints a line like ``  ✓ Logged in to github.com account …``.
# Match with or without the leading checkmark (``gh`` versions differ).
_GH_LOGGED_IN_ACCOUNT_LINE = re.compile(
    r"(?m)^\s*(?:✓\s*)?logged in to\s+\S+\s+account\b",
    re.IGNORECASE,
)
# Copilot-supported token prefixes (classic ``ghp_`` is not supported by Copilot CLI).
_GH_TOKEN_LINE = re.compile(
    r"(?m)^\s*-\s*token:\s*(gho_|github_pat_|ghu_)",
    re.IGNORECASE,
)

# Hard negatives only — avoid ``gh auth login`` alone (could appear in unrelated text).
_GH_LOGGED_OUT_PHRASES = (
    "not logged in",
    "you are not logged into any github hosts",
    "you are not logged into any hosts",
    "no accounts",
)

_AUTH_HINT = (
    "Run `copilot login` or `gh auth login`, or set COPILOT_GITHUB_TOKEN / GH_TOKEN / GITHUB_TOKEN."
)


def _has_token_env() -> str | None:
    """Return the first set token env var name, if any."""
    for key in COPILOT_CLI_ENV_KEYS:
        if os.environ.get(key, "").strip():
            return key
    return None


def _gh_auth_status_argv(gh_bin: str) -> list[str]:
    """Build ``gh auth status`` argv; add ``--hostname`` for non-default GitHub hosts.

    See GitHub Copilot docs (authenticate with GitHub CLI) and ``gh`` docs:
    ``gh auth status --hostname HOST`` for Enterprise / data residency when
    ``github.com`` is not the active host.
    """
    argv: list[str] = [gh_bin, "auth", "status"]
    host = os.environ.get("COPILOT_GH_HOST", "").strip() or os.environ.get("GH_HOST", "").strip()
    if not host:
        return argv
    normalized = host.lower().rstrip("/").removeprefix("https://").removeprefix("http://")
    if normalized in {"", "github.com", "api.github.com"}:
        return argv
    argv.extend(["--hostname", host])
    return argv


def _gh_output_indicates_logged_in(stdout: str, stderr: str) -> bool:
    """Return True when ``gh auth status`` output clearly shows an authenticated host."""
    text = f"{stdout}\n{stderr}"
    lowered = text.lower()
    return (
        "active account: true" in lowered
        or bool(_GH_LOGGED_IN_ACCOUNT_LINE.search(text))
        or bool(_GH_TOKEN_LINE.search(text))
    )


def _classify_gh_auth_status() -> tuple[bool | None, str]:
    """Run ``gh auth status`` and classify its output into the three-state contract.

    Returns ``(logged_in, detail)`` where:
    - ``True``  — ``gh`` clearly reports an active session.
    - ``False`` — ``gh`` clearly reports no accounts / not logged in.
    - ``None``  — ``gh`` not on PATH, spawn failed, timed out, or output is
                  ambiguous (auth then resolved as unknown if no token env).

    Timeouts and errors map to ``None`` (not ``False``) per AGENTS.md: the
    user may be on a flaky network and should not be forced to re-authenticate.
    Negative phrases are checked **before** positive ones to avoid substring
    false-positives (e.g. "not logged in" contains "logged in").
    """
    gh_bin = shutil.which("gh")
    if not gh_bin:
        return None, ""

    argv = _gh_auth_status_argv(gh_bin)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_GH_AUTH_TIMEOUT_SEC,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None, ""

    combined = f"{proc.stdout}\n{proc.stderr}".lower()

    # Check negative phrases first to avoid substring false-positives.
    if any(phrase in combined for phrase in _GH_LOGGED_OUT_PHRASES):
        return False, "gh auth status: not logged in. Run `gh auth login` or set a token env var."

    if _gh_output_indicates_logged_in(proc.stdout or "", proc.stderr or ""):
        return True, "Authenticated via `gh` CLI session (gh auth status)."

    return None, ""


def _classify_copilot_auth() -> tuple[bool | None, str]:
    """Resolve auth state without spawning the Copilot CLI itself.

    Probe order (see module docstring for rationale):
      1. Token env var.
      2. ``gh auth status`` (covers interactive ``gh``-backed login on all platforms).
      3. ``None`` — genuinely unknown.
    """
    token_key = _has_token_env()
    if token_key:
        return True, f"Authenticated via {token_key}."

    gh_logged_in, gh_detail = _classify_gh_auth_status()
    if gh_logged_in is not None:
        return gh_logged_in, gh_detail

    return (
        None,
        f"Could not verify Copilot CLI auth (no token env, gh session not verified or gh not installed). "
        f"{_AUTH_HINT}",
    )


def _fallback_copilot_paths() -> list[str]:
    return _default_cli_fallback_paths("copilot")


class CopilotAdapter:
    """Non-interactive GitHub Copilot CLI (``copilot -p``, programmatic mode)."""

    name = "copilot"
    streams_plain_stdout = True
    binary_env_key = "COPILOT_BIN"
    install_hint = "npm i -g @github/copilot"
    auth_hint = _AUTH_HINT.removesuffix(".")
    min_version: str | None = None
    default_exec_timeout_sec = DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="COPILOT_BIN",
            binary_names=_candidate_binary_names("copilot"),
            fallback_paths=_fallback_copilot_paths,
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
        logged_in, auth_detail = _classify_copilot_auth()
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
                    "Copilot CLI not found on PATH or known install locations. "
                    f"Install with: {self.install_hint} or set COPILOT_BIN."
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
        # Copilot CLI does not expose a reasoning-effort knob; accept the param
        # for protocol parity and discard it (same shape as ClaudeCodeAdapter).
        del reasoning_effort
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                f"Copilot CLI not found. {self.install_hint} "
                "or set COPILOT_BIN to the full binary path."
            )

        ws = (workspace or "").strip()
        cwd = str(Path(ws).expanduser()) if ws else os.getcwd()

        # Each flag is required for a non-interactive run; do not drop these
        # without checking `copilot --help`:
        #   -p PROMPT       enters one-shot mode (without it, copilot opens a TUI).
        #   --no-color      strips ANSI so stdout is parseable.
        #   --no-ask-user   disables the agent's `ask_user` tool, otherwise the
        #                   agent can pause waiting for input even with -p.
        #   --silent        emits only the agent response, not stats / banner.
        argv: list[str] = [
            binary,
            "-p",
            prompt,
            "--no-color",
            "--no-ask-user",
            "--silent",
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["--model", resolved_model])

        # Forward Copilot's config + credential envs exclusively via this
        # invocation env. The global subprocess prefix allowlist deliberately
        # does NOT include ``COPILOT_`` (would leak ``COPILOT_GITHUB_TOKEN``,
        # a GitHub PAT, into every other CLI subprocess).
        env = {
            **nonempty_env_values(COPILOT_CLI_CONFIG_ENV_KEYS),
            **nonempty_env_values(COPILOT_CLI_ENV_KEYS),
        }
        return CLIInvocation(
            argv=tuple(argv),
            stdin=None,
            cwd=cwd,
            env=env or None,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        return require_nonempty_output(stdout, stderr, returncode, self.explain_failure)

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        from integrations.llm_cli.failure_explain import explain_cli_failure

        err = (stderr or "").strip()
        out = (stdout or "").strip()
        text = f"{err}\n{out}".lower()
        auth_markers = (
            "not logged in",
            "not authenticated",
            "no credentials",
            "please /login",
            "unauthorized",
            "401",
        )
        extra = (_AUTH_HINT,) if any(marker in text for marker in auth_markers) else ()
        return explain_cli_failure(
            exit_label="copilot -p",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            extra_messages=extra,
            always_include_output_snippet=True,
        )
