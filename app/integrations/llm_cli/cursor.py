"""Cursor Agent CLI adapter (`agent --print`, non-interactive)."""

from __future__ import annotations

import os
import re
import subprocess

from app.integrations.llm_cli.base import CLIInvocation, CLIProbe
from app.integrations.llm_cli.binary_resolver import (
    candidate_binary_names as _candidate_binary_names,
)
from app.integrations.llm_cli.binary_resolver import (
    default_cli_fallback_paths as _default_cli_fallback_paths,
)
from app.integrations.llm_cli.binary_resolver import resolve_cli_binary

_CURSOR_VERSION_RE = re.compile(r"(\d{4}\.\d{2}\.\d{2}-[a-zA-Z0-9]+|\d+\.\d+\.\d+)")
_PROBE_TIMEOUT_SEC = 3.0


def _parse_version(text: str) -> str | None:
    match = _CURSOR_VERSION_RE.search(text or "")
    return match.group(1) if match else None


class CursorAdapter:
    """Non-interactive Cursor Agent CLI adapter."""

    name = "cursor"
    binary_env_key = "CURSOR_BIN"
    install_hint = "Install Cursor Agent with: curl https://cursor.com/install -fsS | bash"
    auth_hint = "Run: agent login, or set CURSOR_API_KEY."
    min_version: str | None = None
    default_exec_timeout_sec = 300.0

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

        try:
            version_proc = subprocess.run(
                [binary, "--version"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=f"Could not run `{binary} --version`: {exc}",
            )

        if version_proc.returncode != 0:
            err = (version_proc.stderr or version_proc.stdout or "").strip()
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=f"`{binary} --version` failed: {err or 'unknown error'}",
            )

        version_output = version_proc.stdout + version_proc.stderr
        version = _parse_version(version_output)

        if not version:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail="Binary found but does not appear to be Cursor Agent CLI.",
            )

        if os.environ.get("CURSOR_API_KEY"):
            return CLIProbe(
                installed=True,
                version=version,
                logged_in=True,
                bin_path=binary,
                detail="Authenticated via CURSOR_API_KEY environment variable.",
            )

        try:
            status_proc = subprocess.run(
                [binary, "status"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return CLIProbe(
                installed=True,
                version=version,
                logged_in=None,
                bin_path=binary,
                detail=f"Could not verify auth status with `{binary} status`: {exc}",
            )

        status_text = (status_proc.stdout + status_proc.stderr).strip()
        if status_proc.returncode == 0 and "Logged in as" in status_text:
            logged_in: bool | None = True
            detail = status_text
        elif "Not logged in" in status_text or "Authentication required" in status_text:
            logged_in = False
            detail = "Not logged in. Run: agent login, or set CURSOR_API_KEY."
        else:
            logged_in = None
            detail = status_text or "Could not determine Cursor Agent auth status."

        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary,
            detail=detail,
        )

    def build(self, *, prompt: str, model: str | None, workspace: str) -> CLIInvocation:
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

        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
            cwd=ws,
            env=None,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        result = (stdout or "").strip()
        if not result:
            if returncode == 0:
                raise RuntimeError("Cursor Agent CLI returned empty output.")
            raise RuntimeError(
                self.explain_failure(stdout=stdout, stderr=stderr, returncode=returncode)
                + " (empty output)"
            )
        return result

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        err = (stderr or "").strip()
        out = (stdout or "").strip()
        text = f"{err}\n{out}"

        bits = [f"cursor agent exited with code {returncode}"]

        if "Authentication required" in text or "Not logged in" in text:
            bits.append("Not logged in. Run: agent login, or set CURSOR_API_KEY.")
        elif "Workspace Trust Required" in text:
            bits.append("Workspace trust required. The adapter uses --trust for headless runs.")
        elif "Named models unavailable" in text:
            bits.append(
                "Model unavailable for this account. Use CURSOR_MODEL=auto or omit the model override."
            )
        elif err:
            bits.append(err[:2000])
        elif out:
            bits.append(out[:2000])

        return ". ".join(bits)
