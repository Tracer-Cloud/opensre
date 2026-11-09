"""xAI Grok Build CLI adapter (``grok -p``, non-interactive / headless mode).

Grok Build is xAI's terminal-native agentic coding tool (binary: ``grok``). OpenSRE
uses it purely as a one-shot text responder inside the ReAct loop, so invocations
run in headless ``-p`` mode with ``--output-format plain`` and never pass
``--always-approve`` (we do not want Grok autonomously editing files
or running shell commands; OpenSRE provides its own tools).

Env vars
--------
GROK_CLI_BIN              Optional explicit path to the ``grok`` binary.
                          Blank or non-runnable paths are ignored; PATH + fallbacks apply.
GROK_CLI_MODEL            Optional model override (e.g. ``grok-build-0.1``).
                          Unset or empty → omit ``-m``; the CLI's configured default applies.
GROK_CLI_TIMEOUT_SECONDS  Optional invocation timeout override in seconds for long prompts
                          (default: 300, min: 30, max: 600).
XAI_API_KEY               API-key auth for headless/CI runs. Forwarded explicitly to the
                          Grok subprocess via ``CLIInvocation.env`` (see Auth below).

Auth
----
Grok resolves credentials in the order ``model.api_key > model.env_key > active
session token > XAI_API_KEY`` (https://docs.x.ai/build/cli/headless-scripting).
``XAI_API_KEY`` is a secret, so it is forwarded **only** to the Grok subprocess via
``CLIInvocation.env`` rather than the blanket ``_SAFE_SUBPROCESS_ENV_PREFIXES``
allowlist (which would leak it into every other CLI subprocess — same rationale as
the Copilot/Claude Code adapters). There is no documented ``grok auth status``
command, so auth is detected via ``grok models`` — a fast (~0.5 s) subcommand that
prints "You are logged in" on success and doesn't incur an LLM call. Exit 0 with a
login confirmation string → authenticated; auth-error strings in output → not
authenticated; network/timeout → unclear. ``XAI_API_KEY`` in env is treated as
an authenticated fallback for headless/CI runs even when the probe result is unclear.
"""

from __future__ import annotations

import os
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
    XAI_CLI_ENV_KEYS,
    nonempty_env_values,
)
from integrations.llm_cli.output import require_nonempty_output
from integrations.llm_cli.probe_utils import run_version_probe
from integrations.llm_cli.semver_utils import parse_semver_three_part
from integrations.llm_cli.timeout_utils import resolve_timeout_from_env

_PROBE_TIMEOUT_SEC = 5.0
_AUTH_PROBE_TIMEOUT_SEC = 10.0
_AUTH_HINT = "Run: grok login or set XAI_API_KEY."


def _resolve_exec_timeout_seconds() -> float:
    return resolve_timeout_from_env(
        env_key="GROK_CLI_TIMEOUT_SECONDS",
        default=_DEFAULT_EXEC_TIMEOUT_SEC,
        minimum=_MIN_EXEC_TIMEOUT_SEC,
        maximum=_MAX_EXEC_TIMEOUT_SEC,
    )


def _grok_env_overrides() -> dict[str, str]:
    """Subprocess env overrides: disable color and forward xAI API credentials."""
    env: dict[str, str] = {"NO_COLOR": "1"}
    env.update(nonempty_env_values(XAI_CLI_ENV_KEYS))
    return env


def _has_explicit_grok_auth_env() -> str | None:
    """Return the env var name if an explicit xAI API credential is set, else None."""
    if os.environ.get("XAI_API_KEY", "").strip():
        return "XAI_API_KEY"
    return None


def _classify_grok_auth_from_probe(
    returncode: int, stdout: str, stderr: str
) -> tuple[bool | None, str]:
    """Classify auth state from a ``grok models`` probe result.

    ``grok models`` is fast (~0.5 s) and prints "You are logged in" on success,
    making it a reliable auth probe without incurring an LLM call.
    Mirrors the pattern used by the Antigravity CLI adapter.
    """
    text = (stdout + "\n" + stderr).lower()
    if "you are logged in" in text or "logged in with" in text:
        return True, "Authenticated via Grok Build CLI (grok models)."
    if "unauthorized" in text or "not logged in" in text or "please log in" in text:
        return False, f"Not authenticated. {_AUTH_HINT}"
    if "401" in text or ("api key" in text and ("invalid" in text or "missing" in text)):
        return False, f"Authentication failed. {_AUTH_HINT}"
    if returncode == 0:
        return True, "Authenticated via Grok Build CLI."
    if "network" in text or "timeout" in text or "unreachable" in text or "connection" in text:
        return None, "Network error during Grok auth probe; will retry at invocation."
    tail = (stderr or stdout).strip()[:200]
    if tail:
        return None, f"Auth status unclear (exit {returncode}): {tail}"
    return None, f"Auth status unclear (exit {returncode})."


def parse_grok_models_output(text: str) -> list[str]:
    """Parse ``grok models`` stdout into an ordered list of model IDs.

    Example output::

        You are logged in with grok.com.

        Default model: grok-build

        Available models:
          - grok-composer-2.5-fast
          * grok-build (default)

    Returns model IDs in the order listed, default model last (it has ``*``).
    """
    models: list[str] = []
    in_section = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("available models"):
            in_section = True
            continue
        if in_section:
            if stripped.startswith(("- ", "* ")):
                model_id = stripped[2:].split("(")[0].strip()
                if model_id:
                    models.append(model_id)
            elif stripped and not stripped.startswith(("-", "*")):
                break
    return models


def _fallback_grok_paths() -> list[str]:
    return _default_cli_fallback_paths("grok")


class GrokCLIAdapter:
    """Non-interactive xAI Grok Build CLI (``grok -p``, headless mode, no TTY)."""

    name = "grok-cli"
    streams_plain_stdout = True
    binary_env_key = "GROK_CLI_BIN"
    install_hint = "curl -fsSL https://x.ai/cli/install.sh | bash"
    auth_hint = _AUTH_HINT.removesuffix(".")
    min_version: str | None = None
    default_exec_timeout_sec = _DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="GROK_CLI_BIN",
            binary_names=_candidate_binary_names("grok"),
            fallback_paths=_fallback_grok_paths,
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
        # Use the full parent-process env for the probe so grok can reach its
        # keyring / session credentials (DBUS_SESSION_BUS_ADDRESS, DISPLAY, etc.
        # are stripped by build_cli_subprocess_env and cause the process to hang).
        # Merge overrides last so NO_COLOR and XAI_API_KEY always take effect.
        probe_env = {**os.environ, **_grok_env_overrides()}

        try:
            auth_proc = subprocess.run(
                [binary_path, "models"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_AUTH_PROBE_TIMEOUT_SEC,
                check=False,
                env=probe_env,
            )
        except subprocess.TimeoutExpired:
            logged_in: bool | None = None
            auth_detail = (
                f"Grok auth probe timed out after {_AUTH_PROBE_TIMEOUT_SEC:.0f}s; "
                "auth status unknown."
            )
        except OSError as exc:
            logged_in = None
            auth_detail = f"Could not spawn grok for auth probe: {exc}"
        else:
            logged_in, auth_detail = _classify_grok_auth_from_probe(
                auth_proc.returncode, auth_proc.stdout, auth_proc.stderr
            )

        # XAI_API_KEY is definitive for headless/CI auth when the probe result is
        # *unclear* (network error, timeout). Do NOT promote when the probe
        # explicitly returned False — XAI_API_KEY may itself be the rejected
        # credential, and masking that defers the failure to invocation time.
        auth_env_key = _has_explicit_grok_auth_env()
        if logged_in is None and auth_env_key:
            logged_in = True
            auth_detail = f"Authenticated via {auth_env_key}."

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
                    "Grok Build CLI not found on PATH or known install locations. "
                    f"Install with: {self.install_hint} or set GROK_CLI_BIN."
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
        # Grok Build headless mode does not expose a reasoning-effort flag; the
        # parameter is accepted for protocol compatibility and ignored.
        _ = reasoning_effort
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                f"Grok Build CLI not found. {self.install_hint}"
                " or set GROK_CLI_BIN to the full binary path."
            )

        ws = (workspace or "").strip()
        cwd = str(Path(ws).expanduser()) if ws else os.getcwd()

        # `grok -p PROMPT` runs a single headless turn (no TTY). `--output-format
        # plain` yields the model's text answer for parse(). We deliberately omit
        # `--always-approve` so Grok never auto-executes its own tools — OpenSRE
        # drives tool use itself.
        argv: list[str] = [
            binary,
            "-p",
            prompt,
            "--output-format",
            "plain",
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["-m", resolved_model])

        # Forward xAI credentials explicitly rather than via the blanket prefix
        # allowlist, so XAI_API_KEY does not leak into other CLI adapters.
        env = _grok_env_overrides()

        return CLIInvocation(
            argv=tuple(argv),
            stdin=None,
            cwd=cwd,
            env=env,
            timeout_sec=_resolve_exec_timeout_seconds(),
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        return require_nonempty_output(stdout, stderr, returncode, self.explain_failure)

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        from integrations.llm_cli.failure_explain import explain_cli_failure

        # Provider-specific auth message (more actionable than the generic shared
        # hint); quota / context-length / network classification comes from the
        # shared helper so Grok stays consistent with the other CLI adapters.
        lowered = f"{stderr}\n{stdout}".lower()
        extra: tuple[str, ...] = ()
        if "unauthorized" in lowered or "401" in lowered or "not logged in" in lowered:
            extra = (f"Authentication failed. {_AUTH_HINT}",)

        return explain_cli_failure(
            exit_label="grok -p",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            extra_messages=extra,
        )
