"""GitHub Copilot CLI adapter (`copilot -p`, non-interactive)."""

from __future__ import annotations

import re
import subprocess

from app.integrations.llm_cli.base import CLIInvocation, CLIProbe
from app.integrations.llm_cli.binary_resolver import (
    candidate_binary_names as _candidate_binary_names,
)
from app.integrations.llm_cli.binary_resolver import (
    default_cli_fallback_paths as _default_cli_fallback_paths,
)
from app.integrations.llm_cli.binary_resolver import (
    resolve_cli_binary,
)

_COPILOT_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_PROBE_TIMEOUT_SEC = 3.0


def _parse_semver(text: str) -> str | None:
    match = _COPILOT_VERSION_RE.search(text)
    return match.group(1) if match else None


def _fallback_copilot_paths() -> list[str]:
    return _default_cli_fallback_paths("copilot")


def _copilot_auth_detail() -> tuple[bool | None, str]:
    # Copilot CLI docs describe headless auth via tokens; absence of a token is
    # ambiguous because interactive login may also be present in the credential store.
    import os

    token = (
        os.getenv("COPILOT_GITHUB_TOKEN", "").strip()
        or os.getenv("GH_TOKEN", "").strip()
        or os.getenv("GITHUB_TOKEN", "").strip()
    )
    if token:
        return True, "Headless auth token present in environment."
    return (
        None,
        "Auth status unclear. Set COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN, or run copilot login.",
    )


class CopilotAdapter:
    """Non-interactive GitHub Copilot CLI (`copilot -p` with allow-all mode)."""

    name = "copilot"
    binary_env_key = "COPILOT_BIN"
    install_hint = "Install GitHub Copilot CLI and ensure `copilot` is on PATH"
    auth_hint = "Set COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN, or run copilot login"
    min_version: str | None = None
    default_exec_timeout_sec = 120.0

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="COPILOT_BIN",
            binary_names=_candidate_binary_names("copilot"),
            fallback_paths=_fallback_copilot_paths,
        )

    def _probe_binary(self, binary_path: str) -> CLIProbe:
        try:
            version_proc = subprocess.run(
                [binary_path, "--version"],
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
                detail=f"Could not run `{binary_path} --version`: {exc}",
            )

        if version_proc.returncode != 0:
            err = (version_proc.stderr or version_proc.stdout or "").strip()
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=f"`{binary_path} --version` failed: {err or 'unknown error'}",
            )

        version = _parse_semver(version_proc.stdout + version_proc.stderr)
        logged_in, auth_detail = _copilot_auth_detail()
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
                detail="Copilot CLI not found on PATH or known install locations.",
            )
        return self._probe_binary(binary)

    def build(self, *, prompt: str, model: str | None, workspace: str) -> CLIInvocation:
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                "Copilot CLI not found. Install GitHub Copilot CLI or set COPILOT_BIN."
            )

        argv: list[str] = [
            binary,
            "-p",
            prompt,
            "-s",
            "--allow-all",
            "--no-ask-user",
            "--no-color",
        ]
        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["--model", resolved_model])

        if workspace:
            argv.extend(["--add-dir", workspace])

        return CLIInvocation(
            argv=tuple(argv),
            stdin=None,
            cwd=workspace or "",
            env={"COPILOT_ALLOW_ALL": "true"},
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        _ = stderr
        _ = returncode
        return (stdout or "").strip()

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        err = (stderr or "").strip()
        out = (stdout or "").strip()
        bits = [f"copilot exited with code {returncode}"]
        if err:
            bits.append(err[:2000])
        elif out:
            bits.append(out[:2000])
        return ". ".join(bits)
