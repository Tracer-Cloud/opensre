"""OpenAI Codex CLI adapter (`codex exec`, non-interactive)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app.integrations.llm_cli.base import CLIInvocation, CLIProbe, PromptDelivery

_CODEX_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")
_PROBE_TIMEOUT_SEC = 3.0
_READ_ONLY_SANDBOX = "read-only"


def _ver_tuple(version: str) -> tuple[int, int, int]:
    parts = [int(p) for p in version.split(".") if p.isdigit()]
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def _parse_semver(text: str) -> str | None:
    m = _CODEX_VERSION_RE.search(text)
    return m.group(1) if m else None


def _classify_codex_auth(
    returncode: int, stdout: str, stderr: str
) -> tuple[bool | None, str]:
    text = (stdout + "\n" + stderr).lower()
    # Negative phrases first: "logged in" is a substring of "not logged in".
    if "not logged in" in text or "no credentials" in text:
        return False, "Not logged in. Run: codex login"
    if returncode == 0 and "logged in" in text:
        return True, (stdout.strip() or stderr.strip() or "Logged in.").splitlines()[0]
    if "expired" in text or ("invalid" in text and "token" in text):
        return False, "Session expired. Re-authenticate: codex login"
    if "rate limit" in text or "quota" in text:
        return True, "Logged in but rate-limited; try again later."
    if "network" in text or "unreachable" in text or "dns" in text or "connection refused" in text:
        return None, "Network error while checking auth; will retry at invocation."
    if returncode != 0:
        tail = (stderr or stdout).strip()[:200]
        return None, f"Auth status unclear (exit {returncode}): {tail}" if tail else f"Auth status unclear (exit {returncode})."
    return None, "Auth status unknown."


def _codex_workspace_and_skip_git() -> tuple[str, bool]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        root = (proc.stdout or "").strip()
        if proc.returncode == 0 and root:
            return root, False
    except (OSError, subprocess.TimeoutExpired):
        # git missing, not a repo, or timed out — use cwd and let codex skip repo checks.
        pass
    return os.getcwd(), True


def _fallback_codex_paths() -> list[str]:
    home = Path.home()
    if sys.platform == "darwin":
        return [
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
            str(home / ".npm-global/bin/codex"),
            str(home / ".local/bin/codex"),
            str(home / ".volta/bin/codex"),
        ]
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA", "")
        local_appdata = os.getenv("LOCALAPPDATA", "")
        return [
            str(Path(appdata) / "npm" / "codex.cmd"),
            str(Path(local_appdata) / "Programs" / "codex" / "codex.exe"),
        ]
    return [
        str(home / ".npm-global/bin/codex"),
        str(home / ".local/bin/codex"),
        str(home / ".volta/bin/codex"),
        "/usr/local/bin/codex",
    ]


def _is_executable_file(path: str) -> bool:
    p = Path(path)
    return p.is_file() and os.access(p, os.X_OK)


class CodexAdapter:
    """Non-interactive Codex CLI (`codex exec` with read-only sandbox)."""

    name = "codex"
    binary_env_key = "CODEX_BIN"
    install_hint = "npm i -g @openai/codex"
    auth_hint = "Run: codex login"
    min_version: str | None = None
    default_exec_timeout_sec = 900.0
    prompt_delivery: PromptDelivery = "stdin"

    def _resolve_binary(self) -> str | None:
        explicit = os.getenv("CODEX_BIN", "").strip()
        if explicit and Path(explicit).is_file():
            return explicit
        found = shutil.which("codex") or shutil.which("codex.cmd") or shutil.which("codex.ps1")
        if found:
            return found
        for candidate in _fallback_codex_paths():
            if _is_executable_file(candidate):
                return candidate
        return None

    def _probe_binary(self, binary_path: str) -> CLIProbe:
        try:
            ver_proc = subprocess.run(
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

        if ver_proc.returncode != 0:
            err = (ver_proc.stderr or ver_proc.stdout or "").strip()
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail=f"`{binary_path} --version` failed: {err or 'unknown error'}",
            )

        version = _parse_semver(ver_proc.stdout + ver_proc.stderr)
        upgrade_note = ""
        if self.min_version and version and _ver_tuple(version) < _ver_tuple(self.min_version):
            upgrade_note = (
                f" Codex {version} is below tested minimum {self.min_version}; "
                f"upgrade: {self.install_hint}@latest"
            )

        try:
            auth_proc = subprocess.run(
                [binary_path, "login", "status"],
                capture_output=True,
                text=True,
                timeout=_PROBE_TIMEOUT_SEC,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            logged_in: bool | None = None
            auth_detail = "Could not verify login status (timeout or OS error)."
        else:
            logged_in, auth_detail = _classify_codex_auth(
                auth_proc.returncode, auth_proc.stdout, auth_proc.stderr
            )

        detail = auth_detail + upgrade_note
        return CLIProbe(
            installed=True,
            version=version,
            logged_in=logged_in,
            bin_path=binary_path,
            detail=detail.strip(),
        )

    def detect(self) -> CLIProbe:
        binary = self._resolve_binary()
        if not binary:
            return CLIProbe(
                installed=False,
                version=None,
                logged_in=None,
                bin_path=None,
                detail="Codex CLI not found on PATH or known install locations.",
            )
        return self._probe_binary(binary)

    def build(self, *, prompt: str, model: str | None, workspace: str) -> CLIInvocation:
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                "Codex CLI not found. Install with `npm i -g @openai/codex` or set CODEX_BIN."
            )

        ws, skip_git = _codex_workspace_and_skip_git()
        if workspace:
            ws = workspace

        argv: list[str] = [
            binary,
            "exec",
            "--ephemeral",
            "-s",
            _READ_ONLY_SANDBOX,
            "--color",
            "never",
            "-C",
            ws,
        ]
        if skip_git:
            argv.append("--skip-git-repo-check")

        resolved_model = (model or "").strip()
        if resolved_model:
            argv.extend(["-m", resolved_model])

        argv.append("-")

        return CLIInvocation(
            argv=argv,
            stdin=prompt,
            cwd=ws,
            env=None,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        _ = stderr
        _ = returncode
        return (stdout or "").strip()

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        err = (stderr or "").strip()
        out = (stdout or "").strip()
        bits = [f"codex exec exited with code {returncode}"]
        if err:
            bits.append(err[:2000])
        elif out:
            bits.append(out[:2000])
        return ". ".join(bits)
