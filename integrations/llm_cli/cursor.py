"""Cursor Agent CLI adapter (`agent --print`, non-interactive)."""

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
from integrations.llm_cli.binary_resolver import resolve_cli_binary
from integrations.llm_cli.constants import DEFAULT_EXEC_TIMEOUT_SEC
from integrations.llm_cli.env_overrides import CURSOR_CLI_ENV_KEYS, nonempty_env_values
from integrations.llm_cli.output import require_nonempty_output
from integrations.llm_cli.probe_utils import run_version_probe

_CURSOR_VERSION_RE = re.compile(r"(\d{4}\.\d{2}\.\d{2}-[a-zA-Z0-9]+|\d+\.\d+\.\d+)")
# `agent status` often hits the network and prints a short spinner; ~4s locally is common,
# so 3s probes spuriously timed out during wizard/doctor detection.
_PROBE_TIMEOUT_SEC = 15.0


def _parse_version(text: str) -> str | None:
    match = _CURSOR_VERSION_RE.search(text or "")
    return match.group(1) if match else None


def _has_cursor_api_key() -> bool:
    return bool(os.environ.get("CURSOR_API_KEY", "").strip())


def _classify_cursor_auth(returncode: int, stdout: str, stderr: str) -> tuple[bool | None, str]:
    """Map ``agent status`` output to ``logged_in`` + detail (negative phrases first)."""
    text = (stdout + "\n" + stderr).lower()
    # Negative phrases first: "logged in" is a substring of "not logged in".
    if "not logged in" in text or "authentication required" in text:
        return False, "Not logged in. Run: agent login."
    if returncode == 0 and "logged in as" in text:
        line = (stdout.strip() or stderr.strip() or "Logged in.").splitlines()[0]
        return True, line
    if "network" in text or "unreachable" in text or "dns" in text or "connection refused" in text:
        return None, "Network error while checking auth; try again or verify connectivity."
    if returncode != 0:
        tail = (stderr or stdout).strip()[:200]
        return (
            None,
            f"Auth status unclear (exit {returncode}): {tail}"
            if tail
            else f"Auth status unclear (exit {returncode}).",
        )
    combined = stdout.strip() or stderr.strip()
    return None, combined or "Could not determine Cursor Agent auth status."


class CursorAdapter:
    """Non-interactive Cursor Agent CLI adapter (`agent --print`).

    Optional env (see registry ``CURSOR_MODEL``): ``CURSOR_BIN`` explicit binary path,
    ``CURSOR_MODEL`` model override. Headless auth uses ``CURSOR_API_KEY``, merged into
    ``CLIInvocation.env`` via ``env_overrides.CURSOR_CLI_ENV_KEYS`` (runner-safe prefixes still apply).
    """

    name = "cursor"
    streams_plain_stdout = True
    binary_env_key = "CURSOR_BIN"
    install_hint = "Install Cursor Agent with: curl https://cursor.com/install -fsS | bash"
    auth_hint = "Run: agent login."
    min_version: str | None = None
    default_exec_timeout_sec = DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="CURSOR_BIN",
            binary_names=_candidate_binary_names("agent"),
            fallback_paths=_default_cli_fallback_paths("agent"),
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
                    "Cursor Agent CLI not found. Install with "
                    "`curl https://cursor.com/install -fsS | bash` or set CURSOR_BIN."
                ),
            )

        version_output, version_error = run_version_probe(binary, timeout_sec=_PROBE_TIMEOUT_SEC)
        if version_error:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=version_error,
            )

        version = _parse_version(version_output or "")

        if not version:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail="Binary found but does not appear to be Cursor Agent CLI.",
            )

        try:
            status_proc = subprocess.run(
                [binary, "status"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"Could not verify auth status with `{binary} status`: {exc}"
            logged_in: bool | None = None
            if _has_cursor_api_key():
                logged_in = True
                reason = (
                    "timed out" if isinstance(exc, subprocess.TimeoutExpired) else f"failed ({exc})"
                )
                detail = (
                    f"Cursor Agent auth probe {reason}; "
                    "headless auth via CURSOR_API_KEY is configured."
                )
            return CLIProbe(
                installed=True,
                version=version,
                logged_in=logged_in,
                bin_path=binary,
                detail=detail,
            )

        logged_in, detail = _classify_cursor_auth(
            status_proc.returncode, status_proc.stdout, status_proc.stderr
        )
        if logged_in is None and _has_cursor_api_key():
            # Allow API-key auth only when session status is unclear — not when CLI says logged out.
            logged_in = True
            detail = "Auth status unclear, headless auth via environment is configured."

        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary,
            detail=detail,
        )

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
                "Cursor Agent CLI not found. Install with "
                "`curl https://cursor.com/install -fsS | bash` or set CURSOR_BIN."
            )

        ws = workspace or os.getcwd()

        argv: list[str] = [
            binary,
            "--print",
            "--trust",
            "--output-format",
            "text",
            "--workspace",
            ws,
        ]

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["--model", resolved_model])

        cursor_env = nonempty_env_values(CURSOR_CLI_ENV_KEYS)
        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
            cwd=ws,
            env=cursor_env or None,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        empty_output_error = "Cursor Agent CLI returned empty output." if returncode == 0 else None
        return require_nonempty_output(
            stdout,
            stderr,
            returncode,
            self.explain_failure,
            empty_output_error=empty_output_error,
        )

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        from integrations.llm_cli.failure_explain import explain_cli_failure

        err = (stderr or "").strip()
        out = (stdout or "").strip()
        text = f"{err}\n{out}"
        extra: tuple[str, ...] = ()
        if "Authentication required" in text or "Not logged in" in text:
            extra = ("Not logged in. Run: agent login.",)
        elif "Workspace Trust Required" in text:
            extra = ("Workspace trust required. The adapter uses --trust for headless runs.",)
        elif "Named models unavailable" in text:
            extra = (
                "Model unavailable for this account. Use CURSOR_MODEL=auto or omit the model override.",
            )

        return explain_cli_failure(
            exit_label="cursor agent",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
            extra_messages=extra,
        )
