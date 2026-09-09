"""OpenAI Codex CLI adapter (`codex exec`, non-interactive).

OpenAI Platform env vars (``OPENAI_API_KEY``, ``OPENAI_ORG_ID``, ``OPENAI_PROJECT_ID``,
``OPENAI_BASE_URL``) are forwarded on invoke when set, so Codex runs work with
usage-based API key auth as well as ``codex login`` sessions.
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
from integrations.llm_cli.constants import DEFAULT_EXEC_TIMEOUT_SEC
from integrations.llm_cli.env_overrides import (
    OPENAI_PLATFORM_ENV_KEYS,
    nonempty_env_values,
)
from integrations.llm_cli.output import require_nonempty_output
from integrations.llm_cli.probe_utils import run_version_probe
from integrations.llm_cli.semver_utils import parse_semver_three_part, semver_to_tuple

_PROBE_TIMEOUT_SEC = 3.0
_READ_ONLY_SANDBOX = "read-only"
_AUTH_STATUS_PROBE_ENV = "OPENSRE_CODEX_AUTH_STATUS_PROBE"
_TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}


def _classify_codex_auth(returncode: int, stdout: str, stderr: str) -> tuple[bool | None, str]:
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
        return (
            None,
            f"Auth status unclear (exit {returncode}): {tail}"
            if tail
            else f"Auth status unclear (exit {returncode}).",
        )
    return None, "Auth status unknown."


def _codex_workspace_and_skip_git() -> tuple[str, bool]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    return _default_cli_fallback_paths("codex")


def _has_openai_api_key() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY", "").strip())


def _should_probe_codex_login_status() -> bool:
    return os.environ.get(_AUTH_STATUS_PROBE_ENV, "").strip().lower() in _TRUTHY_ENV_VALUES


class CodexAdapter:
    """Non-interactive Codex CLI (`codex exec` with read-only sandbox)."""

    name = "codex"
    streams_plain_stdout = True
    binary_env_key = "CODEX_BIN"
    install_hint = "npm i -g @openai/codex"
    auth_hint = "Run: codex login"
    min_version: str | None = None
    default_exec_timeout_sec = DEFAULT_EXEC_TIMEOUT_SEC

    def _resolve_binary(self) -> str | None:
        return resolve_cli_binary(
            explicit_env_key="CODEX_BIN",
            binary_names=_candidate_binary_names("codex"),
            fallback_paths=_fallback_codex_paths,
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
                f" Codex {version} is below tested minimum {self.min_version}; "
                f"upgrade: {self.install_hint}@latest"
            )

        logged_in: bool | None
        auth_detail: str
        if not _should_probe_codex_login_status():
            logged_in = None
            auth_detail = "Codex CLI installed."
        else:
            try:
                auth_proc = subprocess.run(
                    [binary_path, "login", "status"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=_PROBE_TIMEOUT_SEC,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                logged_in = None
                auth_detail = "Could not verify login status (timeout or OS error)."
            else:
                logged_in, auth_detail = _classify_codex_auth(
                    auth_proc.returncode, auth_proc.stdout, auth_proc.stderr
                )

        if logged_in is not True and _has_openai_api_key():
            # Allow API-key auth when ChatGPT/session login is absent or unclear.
            logged_in = True
            auth_detail = "Authenticated via OPENAI_API_KEY fallback."

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

    def build(
        self,
        *,
        prompt: str,
        model: str | None,
        workspace: str,
        reasoning_effort: str | None = None,
    ) -> CLIInvocation:
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
        if reasoning_effort:
            argv.extend(["-c", f'model_reasoning_effort="{reasoning_effort}"'])

        argv.append("-")

        oai = nonempty_env_values(OPENAI_PLATFORM_ENV_KEYS)
        return CLIInvocation(
            argv=tuple(argv),
            stdin=prompt,
            cwd=ws,
            env=oai or None,
            timeout_sec=self.default_exec_timeout_sec,
        )

    def parse(self, *, stdout: str, stderr: str, returncode: int) -> str:
        return require_nonempty_output(stdout, stderr, returncode, self.explain_failure)

    def explain_failure(self, *, stdout: str, stderr: str, returncode: int) -> str:
        from integrations.llm_cli.failure_explain import explain_cli_failure

        return explain_cli_failure(
            exit_label="codex exec",
            stdout=stdout,
            stderr=stderr,
            returncode=returncode,
        )
