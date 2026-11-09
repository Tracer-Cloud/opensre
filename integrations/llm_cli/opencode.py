"""OpenCode CLI adapter (`opencode run`, non-interactive / one-shot mode).

OpenCode can authenticate via multiple mechanisms (credentials in ``auth.json`` and/or
provider API keys visible in the process environment). We probe auth the same way the
CLI summarizes it: ``opencode auth list`` (alias: ``opencode providers list``), after a
successful ``--version`` check. See ``_parse_opencode_auth_list_output`` for parsing rules.
"""

from __future__ import annotations

import os
import re
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
from integrations.llm_cli.constants import DEFAULT_EXEC_TIMEOUT_SEC
from integrations.llm_cli.env_overrides import (
    HTTP_LLM_PROVIDER_ENV_KEYS,
    nonempty_env_values,
)
from integrations.llm_cli.output import require_nonempty_output
from integrations.llm_cli.probe_utils import run_version_probe
from integrations.llm_cli.semver_utils import parse_semver_three_part

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
_PROBE_TIMEOUT_SEC = 8.0
_AUTH_LIST_TIMEOUT_SEC = 25.0


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _parse_opencode_auth_list_output(raw_stdout: str, raw_stderr: str) -> tuple[bool | None, str]:
    """Map ``opencode auth list`` output to ``logged_in`` + human detail.

    OpenCode prints a summary table (file-backed credentials under ``auth.json`` and
    optional environment-backed provider keys). We require a ``<n> credentials`` line;
    ``<n> environment variable(s)`` is optional and defaults to 0 when absent.
    """
    combined = _strip_ansi((raw_stdout or "") + "\n" + (raw_stderr or ""))
    cred_m = re.search(r"(\d+)\s+credentials\b", combined)
    if not cred_m:
        tail = combined.strip()[-400:]
        return (
            None,
            "Could not parse `opencode auth list` output (missing credentials summary)."
            + (f" Tail: {tail!r}" if tail else ""),
        )

    creds = int(cred_m.group(1))
    env_m = re.search(r"(\d+)\s+environment variables?\b", combined)
    envs = int(env_m.group(1)) if env_m else 0

    if creds >= 1 or envs >= 1:
        parts: list[str] = []
        if creds >= 1:
            parts.append(f"{creds} credential group(s) in auth store")
        if envs >= 1:
            parts.append(f"{envs} environment provider key(s)")
        return True, "OpenCode: " + "; ".join(parts) + ". (See `opencode auth list`.)"

    return (
        False,
        "OpenCode reports no file credentials and no provider keys in environment. "
        "Run: opencode auth login — or export a supported provider API key.",
    )


def _probe_opencode_auth_via_cli(binary_path: str) -> tuple[bool | None, str]:
    """Run ``opencode auth list`` with the same environment as the parent process.

    Inherits the full environment so the CLI can detect API keys (e.g. ``ANTHROPIC_API_KEY``)
    the same way it does interactively. Uses ``NO_COLOR=1`` to simplify parsing.
    """
    env = os.environ.copy()
    env["NO_COLOR"] = "1"

    try:
        list_proc = subprocess.run(
            [binary_path, "auth", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_AUTH_LIST_TIMEOUT_SEC,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return (
            None,
            "`opencode auth list` timed out (first run can migrate local data). "
            "Retry once OpenCode finishes initializing.",
        )
    except OSError as exc:
        return None, f"Could not run `opencode auth list`: {exc}"

    if list_proc.returncode != 0:
        err = _strip_ansi((list_proc.stderr or list_proc.stdout or "").strip())
        tail = (err or f"exit {list_proc.returncode}")[:400]
        return None, f"`opencode auth list` failed (exit {list_proc.returncode}): {tail}"

    return _parse_opencode_auth_list_output(list_proc.stdout, list_proc.stderr)


def _fallback_opencode_paths() -> list[str]:
    return _default_cli_fallback_paths("opencode")


class OpenCodeAdapter:
    """Non-interactive OpenCode CLI (`opencode run`, one-shot execution)."""

    name = "opencode"
    streams_plain_stdout = True
    binary_env_key = "OPENCODE_BIN"
    install_hint = (
        "brew install anomalyco/tap/opencode  (macOS/Linux) | choco install opencode (Windows)"
    )
    auth_hint = "Run: opencode auth login (interactive) or configure provider API keys / auth.json"
    min_version: str | None = None
    default_exec_timeout_sec = DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="OPENCODE_BIN",
            binary_names=_candidate_binary_names("opencode"),
            fallback_paths=_fallback_opencode_paths,
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
        logged_in, auth_detail = _probe_opencode_auth_via_cli(binary_path)

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
                    "OpenCode CLI not found on PATH or known install locations. "
                    f"Install with: {self.install_hint}  or set OPENCODE_BIN."
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
                f"OpenCode CLI not found. {self.install_hint}"
                " or set OPENCODE_BIN to the full binary path."
            )

        cwd = workspace or os.getcwd()

        argv: list[str] = [
            binary,
            "run",
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["-m", resolved_model])

        env: dict[str, str] = {"NO_COLOR": "1"}
        env.update(nonempty_env_values(HTTP_LLM_PROVIDER_ENV_KEYS))
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            val = os.environ.get(key, "").strip()
            if val:
                env[key] = val

        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
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
        combined = (err + " " + out).lower()
        extra: tuple[str, ...] = ()
        if "not authenticated" in combined or ("auth" in combined and "failed" in combined):
            extra = ("Authentication failed. Run: opencode auth login",)
        elif "model" in combined and ("not found" in combined or "invalid" in combined):
            extra = (
                "Model not found. Check OPENCODE_MODEL format: "
                "provider/model (e.g., openai/gpt-5.4-mini)",
            )
        elif "rate limit" in combined or "quota" in combined:
            extra = ("Rate limited or quota exceeded. Try again later or check your provider plan",)

        return explain_cli_failure(
            exit_label="opencode run",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            extra_messages=extra,
            always_include_output_snippet=bool(extra),
        )
