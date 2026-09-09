"""Pi CLI adapter (``pi -p``, non-interactive / print mode).

Pi (https://pi.dev, repo: earendil-works/pi) is an open-source, bring-your-own-key
coding-agent CLI that runs the same agent loop against ~30 providers (Anthropic,
OpenAI, Google Gemini, xAI, DeepSeek, …). OpenSRE uses it purely as a one-shot
text responder inside the ReAct loop, so invocations run in headless ``-p`` print
mode (no TTY, no approval prompts).

Env vars
--------
PI_BIN     Optional explicit path to the ``pi`` binary. Blank or non-runnable
           paths are ignored; PATH + fallbacks still apply.
PI_MODEL   Optional model override in Pi's ``provider/model`` form
           (e.g. ``google/gemini-2.5-flash-lite``, ``anthropic/claude-haiku``).
           Unset or empty → omit ``--model`` and the CLI's configured default
           applies. Registered as ``model_env_key`` in ``registry.py``.

Per-provider API keys (``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``,
``GEMINI_API_KEY``, …) are forwarded to the Pi subprocess via
``CLIInvocation.env`` (see ``PI_PROVIDER_ENV_KEYS`` in ``env_overrides.py``).

Auth probe
----------
Pi exposes **no** non-interactive auth-status command — ``/login`` / ``/logout``
are interactive TUI slash commands only. But Pi stores credentials in a readable
file, so (mirroring the Kimi / Claude Code adapters) we detect auth from state
rather than a probe subprocess. Resolution order:

1. A supported provider API key in the environment → ``True`` (BYOK / headless).
2. ``~/.pi/agent/auth.json`` present with credential content → ``True``. This is
   the signal for users who authenticated via ``/login`` (OAuth subscriptions or
   stored API keys) and have no provider key exported.
3. Neither present → ``False`` (run ``pi`` and ``/login``, or export a key).
4. Credential file present but unreadable / unparseable → ``None`` (unclear;
   invocation will verify).

This matches Pi's own credential-resolution priority (``--api-key`` flag >
``auth.json`` > env var) and avoids depending on the undocumented auth behavior
of ``pi --list-models``.
"""

from __future__ import annotations

import json
import os
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
    PI_PROVIDER_ENV_KEYS,
    nonempty_env_values,
)
from integrations.llm_cli.output import require_nonempty_output
from integrations.llm_cli.probe_utils import run_version_probe
from integrations.llm_cli.semver_utils import parse_semver_three_part

_PROBE_TIMEOUT_SEC = 8.0
_AUTH_HINT = "Run `pi` then `/login`, or export a provider API key (e.g. GEMINI_API_KEY)."


def _pi_agent_dir() -> Path:
    """Return Pi's agent config dir, honoring ``PI_AGENT_DIR`` / ``PI_CONFIG_DIR``.

    Defaults to ``~/.pi/agent`` (where Pi stores ``auth.json``). The override env
    var names are best-effort; whatever ``PI_*`` dir var Pi uses is also forwarded
    to the subprocess via the ``PI_`` prefix allowlist.
    """
    override = (
        os.environ.get("PI_AGENT_DIR", "").strip() or os.environ.get("PI_CONFIG_DIR", "").strip()
    )
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pi" / "agent"


def _has_provider_api_key() -> str | None:
    """Return the name of the first supported provider API key set in env, else None."""
    for key in PI_PROVIDER_ENV_KEYS:
        if os.environ.get(key, "").strip():
            return key
    return None


def _auth_json_has_credentials() -> tuple[bool | None, str]:
    """Inspect ``<agent-dir>/auth.json`` for stored credentials.

    Returns ``(True, detail)`` when the file holds at least one credential entry,
    ``(False, detail)`` when it is absent, and ``(None, detail)`` when it exists
    but cannot be read or parsed (auth state unclear).
    """
    auth_path = _pi_agent_dir() / "auth.json"
    try:
        if not auth_path.exists():
            return False, f"No credentials at {auth_path}. {_AUTH_HINT}"
        raw = auth_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return None, f"Could not read {auth_path}: {exc}"

    if not raw:
        return False, f"{auth_path} is empty. {_AUTH_HINT}"

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        return None, f"Could not parse {auth_path}: {exc}"

    # Pi stores a JSON object keyed by provider/credential; any non-empty entry
    # means the user has logged in or stored a key. Be permissive about the exact
    # schema (it may evolve) — presence of content is the signal.
    if isinstance(data, dict) and any(data.values()):
        return True, "Authenticated via ~/.pi/agent/auth.json (pi /login)."
    if isinstance(data, list) and data:
        return True, "Authenticated via ~/.pi/agent/auth.json (pi /login)."
    return False, f"No credential entries in {auth_path}. {_AUTH_HINT}"


def _classify_pi_auth() -> tuple[bool | None, str]:
    """Resolve Pi auth state from env keys then the stored credential file."""
    api_key = _has_provider_api_key()
    if api_key:
        return True, f"Authenticated via {api_key}."
    return _auth_json_has_credentials()


def _pi_env_overrides() -> dict[str, str]:
    """Subprocess env overrides: disable color and forward provider API keys."""
    env: dict[str, str] = {"NO_COLOR": "1"}
    env.update(nonempty_env_values(PI_PROVIDER_ENV_KEYS))
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        val = os.environ.get(key, "").strip()
        if val:
            env[key] = val
    return env


def _fallback_pi_paths() -> list[str]:
    return _default_cli_fallback_paths("pi")


class PiAdapter:
    """Non-interactive Pi CLI (``pi -p``, print mode, no TTY)."""

    name = "pi"
    streams_plain_stdout = True
    binary_env_key = "PI_BIN"
    install_hint = "npm i -g @earendil-works/pi-coding-agent"
    auth_hint = _AUTH_HINT.removesuffix(".")
    min_version: str | None = None
    default_exec_timeout_sec = DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="PI_BIN",
            binary_names=_candidate_binary_names("pi"),
            fallback_paths=_fallback_pi_paths,
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
        logged_in, auth_detail = _classify_pi_auth()
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
                    "Pi CLI not found on PATH or known install locations. "
                    f"Install with: {self.install_hint} or set PI_BIN."
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
        # Pi print mode has no reasoning-effort flag (thinking level is part of
        # the model string, e.g. ``sonnet:high``); accept the param for protocol
        # parity and discard it.
        _ = reasoning_effort
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                f"Pi CLI not found. {self.install_hint} or set PI_BIN to the full binary path."
            )

        ws = (workspace or "").strip()
        cwd = str(Path(ws).expanduser()) if ws else os.getcwd()

        # ``pi -p PROMPT`` runs a single non-interactive turn (no TTY) and prints
        # the model's answer to stdout for parse(). Prompt is passed as an argv
        # arg (the documented print-mode form). Pi keeps its default tools and
        # context-file (AGENTS.md / CLAUDE.md) discovery enabled, matching the
        # other default-agent CLI adapters (claude-code, opencode, gemini-cli).
        argv: list[str] = [
            binary,
            "-p",
            prompt,
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["--model", resolved_model])

        env = _pi_env_overrides()

        return CLIInvocation(
            argv=tuple(argv),
            stdin=None,
            cwd=cwd,
            env=env,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        return require_nonempty_output(stdout, stderr, returncode, self.explain_failure)

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        from integrations.llm_cli.failure_explain import explain_cli_failure

        err = (stderr or "").strip()
        out = (stdout or "").strip()
        combined = f"{err}\n{out}".lower()
        extra: tuple[str, ...] = ()
        if (
            "not logged in" in combined
            or "not authenticated" in combined
            or "no credentials" in combined
            or "unauthorized" in combined
            or "401" in combined
            or ("api key" in combined and ("invalid" in combined or "missing" in combined))
        ):
            extra = (f"Authentication failed. {_AUTH_HINT}",)
        elif "model" in combined and ("not found" in combined or "invalid" in combined):
            extra = (
                "Model not found. Check PI_MODEL format: provider/model "
                "(e.g. google/gemini-2.5-flash-lite).",
            )
        elif "rate limit" in combined or "quota" in combined:
            extra = (
                "Rate limited or quota exceeded. Try again later or check your provider plan.",
            )

        return explain_cli_failure(
            exit_label="pi -p",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            extra_messages=extra,
            always_include_output_snippet=bool(extra),
        )
