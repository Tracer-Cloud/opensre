"""Google Antigravity CLI adapter (``agy -p``, non-interactive headless mode).

Antigravity CLI is the successor to Gemini CLI. Gemini CLI stops serving Pro/Ultra
and free users on 2026-06-18; paid Gemini Code Assist users keep Gemini CLI.

Env vars
--------
ANTIGRAVITY_CLI_BIN              Optional explicit path to the ``agy`` binary.
ANTIGRAVITY_CLI_TIMEOUT_SECONDS  Optional invocation timeout override (clamped 30–600s).

``ANTIGRAVITY_CLI_MODEL`` is registered for forward-compat on the registry but is
**no-op** today: ``agy`` v1.0.2 does not expose ``--model`` in headless ``-p`` mode
(verified locally). Each invocation uses whatever model is persisted in agy's
local config; users change it interactively with ``/models`` inside the REPL.
Once Google ships ``--model`` in headless, ``build()`` can forward the env var
in a one-line change (see the Known gap note near ``del model``).

Auth
----
Google Sign-In via browser OAuth on first interactive ``agy`` run; the token is
cached by the OS keyring. No documented ``ANTIGRAVITY_API_KEY``. As a best-effort
fallback, the probe treats explicit ``GEMINI_API_KEY`` / ``GOOGLE_API_KEY`` /
Vertex env credentials as authenticated, mirroring ``gemini_cli.py``.

Stateless invocation
--------------------
``build()`` never passes ``--continue`` / ``--conversation`` / ``--sandbox`` /
``--dangerously-skip-permissions``; each opensre call is ephemeral. ``--output-format``
was removed between Gemini CLI and Antigravity CLI — stdout is plain text now.
"""

from __future__ import annotations

import os
import subprocess

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
from integrations.llm_cli.output import require_nonempty_output
from integrations.llm_cli.probe_utils import run_version_probe
from integrations.llm_cli.semver_utils import parse_semver_three_part, semver_to_tuple
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env
from integrations.llm_cli.timeout_utils import resolve_timeout_from_env

_PROBE_TIMEOUT_SEC = 20.0
_AUTH_HINT = "Run: agy (interactive Google Sign-In) or set GEMINI_API_KEY for keyless fallback."
# Buffer so the Python-side subprocess timeout sits above ``agy --print-timeout``
# and lets the CLI emit its own clean timeout message instead of being SIGKILL'd.
_SUBPROCESS_TIMEOUT_BUFFER_SEC = 10.0


def _resolve_exec_timeout_seconds() -> float:
    return resolve_timeout_from_env(
        env_key="ANTIGRAVITY_CLI_TIMEOUT_SECONDS",
        default=_DEFAULT_EXEC_TIMEOUT_SEC,
        minimum=_MIN_EXEC_TIMEOUT_SEC,
        maximum=_MAX_EXEC_TIMEOUT_SEC,
    )


def _antigravity_auth_env_overrides() -> dict[str, str]:
    """Build agy subprocess auth/config overrides used by probe and invoke."""
    env: dict[str, str] = {"NO_COLOR": "1"}
    keys = (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
    )
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            env[key] = val
    return env


def _has_explicit_antigravity_auth_env() -> str | None:
    env = _antigravity_auth_env_overrides()
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        if env.get(key):
            return key
    if env.get("GOOGLE_GENAI_USE_VERTEXAI") and env.get("GOOGLE_CLOUD_PROJECT"):
        return "GOOGLE_GENAI_USE_VERTEXAI"
    return None


def _classify_antigravity_auth(
    returncode: int, stdout: str, stderr: str
) -> tuple[bool | None, str]:
    text = (stdout + "\n" + stderr).lower()
    if "not authenticated" in text or ("authentication" in text and "required" in text):
        return False, f"Not authenticated. {_AUTH_HINT}"
    if "login required" in text or "please login" in text or "please sign in" in text:
        return False, f"Not authenticated. {_AUTH_HINT}"
    if "please set an auth method" in text:
        return False, f"Not authenticated. {_AUTH_HINT}"
    if "invalid api key" in text or ("api key" in text and "missing" in text):
        return (
            False,
            "Antigravity API key missing or invalid. Set GEMINI_API_KEY or run `agy` to sign in.",
        )
    if returncode == 0:
        return True, "Authenticated via Antigravity CLI."
    if "network" in text or "timeout" in text or "unreachable" in text or "connection" in text:
        return None, "Network error while checking auth; will retry at invocation."
    tail = (stderr or stdout).strip()[:200]
    if tail:
        return None, f"Auth status unclear (exit {returncode}): {tail}"
    return None, f"Auth status unclear (exit {returncode})."


def _fallback_antigravity_cli_paths() -> list[str]:
    return _default_cli_fallback_paths("agy")


class AntigravityCLIAdapter:
    """Non-interactive Antigravity CLI (``agy -p`` headless mode)."""

    name = "antigravity-cli"
    streams_plain_stdout = True
    binary_env_key = "ANTIGRAVITY_CLI_BIN"
    install_hint = "curl -fsSL https://antigravity.google/cli/install.sh | bash"
    auth_hint = _AUTH_HINT.removesuffix(".")
    # 1.0.0 had OAuth-hang bugs fixed in 1.0.1; flag older installs at probe time.
    min_version: str | None = "1.0.1"
    default_exec_timeout_sec = _DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="ANTIGRAVITY_CLI_BIN",
            binary_names=_candidate_binary_names("agy"),
            fallback_paths=_fallback_antigravity_cli_paths,
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
        upgrade_note = ""
        if (
            self.min_version
            and version
            and semver_to_tuple(version) < semver_to_tuple(self.min_version)
        ):
            upgrade_note = (
                f" Antigravity CLI {version} is below tested minimum {self.min_version}; "
                "upgrade: agy update"
            )

        probe_env = build_cli_subprocess_env(_antigravity_auth_env_overrides())
        try:
            auth_proc = subprocess.run(
                [binary_path, "-p", "respond with: ok", "--print-timeout", "15s"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
                env=probe_env,
            )
        except subprocess.TimeoutExpired:
            logged_in: bool | None = None
            auth_detail = (
                f"Antigravity auth probe timed out after {_PROBE_TIMEOUT_SEC:.0f}s; "
                "auth status unknown."
            )
        except OSError as exc:
            logged_in = None
            auth_detail = f"Could not spawn agy for auth probe: {exc}"
        else:
            logged_in, auth_detail = _classify_antigravity_auth(
                auth_proc.returncode, auth_proc.stdout, auth_proc.stderr
            )

        auth_env_source = _has_explicit_antigravity_auth_env()
        if logged_in is not True and auth_env_source:
            logged_in = True
            auth_detail = f"Authenticated via {auth_env_source} fallback."

        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary_path,
            detail=auth_detail + upgrade_note,
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
                    "Antigravity CLI (`agy`) not found on PATH or known install locations. "
                    f"Install with: {self.install_hint} or set ANTIGRAVITY_CLI_BIN."
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
        # Known gap: agy 1.0.2 does not expose ``--model`` or reasoning knobs in
        # headless ``-p`` mode. Each invocation uses whatever model is persisted in
        # agy's local config; users change it via ``/models`` inside the REPL.
        # ``model`` and ``reasoning_effort`` are accepted for protocol compatibility
        # but deliberately ignored here.
        # Once agy ships ``--model`` in headless, forward the env var here (the
        # docstring near ``ANTIGRAVITY_CLI_MODEL`` at the top of this module
        # describes the upgrade path).
        del model, reasoning_effort

        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                f"Antigravity CLI not found. {self.install_hint} "
                "or set ANTIGRAVITY_CLI_BIN to the full binary path."
            )

        resolved_timeout = _resolve_exec_timeout_seconds()
        argv: list[str] = [
            binary,
            "-p",
            prompt,
            "--print-timeout",
            f"{int(resolved_timeout)}s",
        ]

        ws = (workspace or "").strip()
        cwd = ws or os.getcwd()
        env = _antigravity_auth_env_overrides()

        return CLIInvocation(
            argv=tuple(argv),
            stdin=None,
            cwd=cwd,
            env=env,
            timeout_sec=resolved_timeout + _SUBPROCESS_TIMEOUT_BUFFER_SEC,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        return require_nonempty_output(stdout, stderr, returncode, self.explain_failure)

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        from integrations.llm_cli.failure_explain import explain_cli_failure

        return explain_cli_failure(
            exit_label="agy -p",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )
