"""GitHub Copilot CLI adapter (`copilot -p`, non-interactive)."""

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
    # ambiguous because interactive login may also be present in the credential
    # store. This helper only checks env vars; interactive status is probed by
    # calling `copilot auth status` from `_probe_binary` so callers here treat
    # the result as a quick env-based hint.
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


def _classify_copilot_auth(returncode: int, stdout: str, stderr: str) -> tuple[bool | None, str]:
    text = (stdout + "\n" + stderr).lower()
    if "not logged in" in text or "no credentials" in text:
        return False, "Not logged in. Run: copilot login"
    if returncode == 0 and ("logged in" in text or "authenticated" in text):
        return True, (stdout.strip() or stderr.strip() or "Logged in.").splitlines()[0]
    if "expired" in text or ("invalid" in text and "token" in text):
        return False, "Session expired. Re-authenticate: copilot login"
    if "network" in text or "unreachable" in text or "dns" in text or "connection refused" in text:
        return None, "Network error while checking auth; will retry at invocation."
    if returncode != 0:
        tail = (stderr or stdout).strip()[:200]
        return (
            None,
            f"Auth status unclear (exit {returncode}): {tail}"
            if tail
            else f"Auth status unclear (exit {returncode}).",
        )
    return None, "Auth status unknown."


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
        # First, check env vars quickly
        logged_in, auth_detail = _copilot_auth_detail()
        # Then try probing interactive auth status via `copilot auth status`.
        try:
            auth_proc = subprocess.run(
                [binary_path, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            # Could not verify interactive login; keep env-based result.
            pass
        else:
            # If env-based was ambiguous, prefer the CLI auth check.
            if logged_in is None:
                logged_in, auth_detail = _classify_copilot_auth(
                    auth_proc.returncode, auth_proc.stdout, auth_proc.stderr
                )
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

        # Ensure subprocess cwd is a valid path (subprocess rejects empty string).
        cwd = workspace if workspace else os.getcwd()

        return CLIInvocation(
            argv=tuple(argv),
            stdin=None,
            cwd=cwd,
            env={"COPILOT_ALLOW_ALL": "true"},
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:  # noqa: ARG002
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
