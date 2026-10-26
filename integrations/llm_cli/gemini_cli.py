"""Google Gemini CLI adapter (``gemini -p``, non-interactive headless mode).

Env vars
--------
GEMINI_CLI_BIN   Optional explicit path to the ``gemini`` binary.
GEMINI_CLI_MODEL Optional model override passed as ``--model``.
GEMINI_CLI_TIMEOUT_SECONDS Optional invocation timeout override for long prompts.

Auth
----
Gemini CLI supports multiple auth modes (cached login sessions, ``GEMINI_API_KEY``,
Vertex env credentials). Probe classification uses a short headless call and
maps outcomes to ``logged_in``: True / False / None.
"""

from __future__ import annotations

import json
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
from integrations.llm_cli.semver_utils import parse_semver_three_part
from integrations.llm_cli.subprocess_env import build_cli_subprocess_env
from integrations.llm_cli.timeout_utils import resolve_timeout_from_env

_PROBE_TIMEOUT_SEC = 20.0
_AUTH_HINT = "Run: gemini (interactive login) or set GEMINI_API_KEY."
# Source: https://developers.googleblog.com/an-important-update-transitioning-gemini-cli-to-antigravity-cli/
_SUNSET_NOTE = (
    " Note: Gemini CLI stops serving Pro/Ultra and free users on 2026-06-18; "
    "paid Gemini Code Assist remains supported. Consider antigravity-cli (agy)."
)


def _resolve_exec_timeout_seconds() -> float:
    return resolve_timeout_from_env(
        env_key="GEMINI_CLI_TIMEOUT_SECONDS",
        default=_DEFAULT_EXEC_TIMEOUT_SEC,
        minimum=_MIN_EXEC_TIMEOUT_SEC,
        maximum=_MAX_EXEC_TIMEOUT_SEC,
    )


def _gemini_auth_env_overrides() -> dict[str, str]:
    """Build Gemini subprocess auth/config overrides used by probe and invoke."""
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


def _has_explicit_gemini_auth_env() -> str | None:
    env = _gemini_auth_env_overrides()
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS"):
        if env.get(key):
            return key
    if env.get("GOOGLE_GENAI_USE_VERTEXAI") and env.get("GOOGLE_CLOUD_PROJECT"):
        return "GOOGLE_GENAI_USE_VERTEXAI"
    return None


def _classify_gemini_auth(returncode: int, stdout: str, stderr: str) -> tuple[bool | None, str]:
    raw = (stdout or "").strip()
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict):
            err = payload.get("error")
            if isinstance(err, dict):
                message = str(err.get("message", "")).strip()
                code = err.get("code")
                msg_lower = message.lower()
                if (
                    "please set an auth method" in msg_lower
                    or "gemini_api_key" in msg_lower
                    or "not authenticated" in msg_lower
                    or "login required" in msg_lower
                    or code == 41
                ):
                    return False, f"Not authenticated. {_AUTH_HINT}"
    text = (stdout + "\n" + stderr).lower()
    if "not authenticated" in text or ("authentication" in text and "required" in text):
        return False, f"Not authenticated. {_AUTH_HINT}"
    if "login required" in text or "please login" in text:
        return False, f"Not authenticated. {_AUTH_HINT}"
    if "please set an auth method" in text:
        return False, f"Not authenticated. {_AUTH_HINT}"
    if "invalid api key" in text or ("api key" in text and "missing" in text):
        return False, "Gemini API key missing or invalid. Set GEMINI_API_KEY or login via `gemini`."
    if returncode == 0:
        return True, "Authenticated via Gemini CLI."
    if "network" in text or "timeout" in text or "unreachable" in text or "connection" in text:
        return None, "Network error while checking auth; will retry at invocation."
    tail = (stderr or stdout).strip()[:200]
    if tail:
        return None, f"Auth status unclear (exit {returncode}): {tail}"
    return None, f"Auth status unclear (exit {returncode})."


def _fallback_gemini_cli_paths() -> list[str]:
    return _default_cli_fallback_paths("gemini")


class GeminiCLIAdapter:
    """Non-interactive Gemini CLI (``gemini -p`` headless mode)."""

    name = "gemini-cli"
    binary_env_key = "GEMINI_CLI_BIN"
    install_hint = "npm i -g @google/gemini-cli"
    auth_hint = _AUTH_HINT.removesuffix(".")
    min_version: str | None = None
    default_exec_timeout_sec = _DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="GEMINI_CLI_BIN",
            binary_names=_candidate_binary_names("gemini"),
            fallback_paths=_fallback_gemini_cli_paths,
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
        probe_env = build_cli_subprocess_env(_gemini_auth_env_overrides())
        try:
            auth_proc = subprocess.run(
                [binary_path, "-p", "respond with: ok", "--output-format", "json"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
                env=probe_env,
            )
        except subprocess.TimeoutExpired:
            logged_in = None
            auth_detail = (
                f"Gemini auth probe timed out after {_PROBE_TIMEOUT_SEC:.0f}s; auth status unknown."
            )
        except OSError as exc:
            logged_in = None
            auth_detail = f"Could not spawn gemini for auth probe: {exc}"
        else:
            logged_in, auth_detail = _classify_gemini_auth(
                auth_proc.returncode, auth_proc.stdout, auth_proc.stderr
            )

        auth_env_source = _has_explicit_gemini_auth_env()
        if logged_in is not True and auth_env_source:
            logged_in = True
            auth_detail = f"Authenticated via {auth_env_source} fallback."

        # Gate the sunset notice on ``logged_in is not False`` so a user mid
        # auth-failure isn't shown the migration ad next to the actual error
        # they need to read. Authenticated (True) and ambiguous (None) probes
        # still surface it — those are the cohorts whose CLI is operational
        # today and who need to plan for 2026-06-18.
        detail = auth_detail + _SUNSET_NOTE if logged_in is not False else auth_detail

        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary_path,
            detail=detail,
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
                    "Gemini CLI not found on PATH or known install locations. "
                    f"Install with: {self.install_hint} or set GEMINI_CLI_BIN."
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
                f"Gemini CLI not found. {self.install_hint} "
                "or set GEMINI_CLI_BIN to the full binary path."
            )

        argv: list[str] = [binary, "-p", prompt, "--output-format", "json"]
        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["--model", resolved_model])

        ws = (workspace or "").strip()
        cwd = ws or os.getcwd()
        env = _gemini_auth_env_overrides()

        return CLIInvocation(
            argv=tuple(argv),
            stdin=None,
            cwd=cwd,
            env=env,
            timeout_sec=_resolve_exec_timeout_seconds(),
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        text = require_nonempty_output(stdout, stderr, returncode, self.explain_failure)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(payload, dict):
            response = payload.get("response")
            if isinstance(response, str):
                return response.strip()
            err = payload.get("error")
            if isinstance(err, dict):
                message = err.get("message")
                if isinstance(message, str) and message.strip():
                    raise RuntimeError(f"Gemini CLI returned an error: {message.strip()}")
        return text

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        from integrations.llm_cli.failure_explain import explain_cli_failure

        return explain_cli_failure(
            exit_label="gemini -p",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )
