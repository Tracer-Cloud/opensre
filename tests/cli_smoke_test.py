from __future__ import annotations

import errno
import json
import os
import re
import select
import shutil
import site
import subprocess
import sys
import sysconfig
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from app.version import get_version

REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_NAME = "opensre.exe" if os.name == "nt" else "opensre"
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_CLEARED_ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_DEFAULT_REGION",
    "AWS_EXTERNAL_ID",
    "AWS_REGION",
    "AWS_ROLE_ARN",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "CORALOGIX_API_KEY",
    "CORALOGIX_API_URL",
    "CORALOGIX_APPLICATION_NAME",
    "CORALOGIX_SUBSYSTEM_NAME",
    "DD_API_KEY",
    "DD_APP_KEY",
    "DD_SITE",
    "GEMINI_API_KEY",
    "GOOGLE_CREDENTIALS_FILE",
    "GOOGLE_DRIVE_FOLDER_ID",
    "GRAFANA_INSTANCE_URL",
    "GRAFANA_READ_TOKEN",
    "HONEYCOMB_API_KEY",
    "HONEYCOMB_API_URL",
    "HONEYCOMB_DATASET",
    "JWT_TOKEN",
    "NVIDIA_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENSRE_PROJECT_ENV_PATH",
    "OPENSRE_RELEASES_API_URL",
    "SLACK_WEBHOOK_URL",
    "TRACER_API_URL",
    "TRACER_WEB_APP_URL",
)


@dataclass(frozen=True)
class CliResult:
    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str = ""


@dataclass(frozen=True)
class PtyAction:
    expect: str
    send: bytes
    timeout: float = 10.0


@dataclass
class CliSandbox:
    home: Path
    project_env_path: Path
    env: dict[str, str]

    @property
    def integration_store_path(self) -> Path:
        return self.home / ".tracer" / "integrations.json"

    @property
    def wizard_store_path(self) -> Path:
        return self.home / ".opensre" / "opensre.json"

    def seed_integrations(self, integrations: list[dict[str, object]]) -> None:
        self.integration_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.integration_store_path.write_text(
            json.dumps({"version": 1, "integrations": integrations}, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_integrations(self) -> list[dict[str, object]]:
        if not self.integration_store_path.exists():
            return []
        payload = json.loads(self.integration_store_path.read_text(encoding="utf-8"))
        return list(payload.get("integrations", []))

    def read_project_env(self) -> str:
        if not self.project_env_path.exists():
            return ""
        return self.project_env_path.read_text(encoding="utf-8")

    def read_wizard_store(self) -> dict[str, object]:
        return json.loads(self.wizard_store_path.read_text(encoding="utf-8"))


def _clean_terminal_output(text: str) -> str:
    if not text:
        return ""
    cleaned = _ANSI_RE.sub("", text)
    cleaned = cleaned.replace("\r", "\n").replace("\x00", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _opensre_executable() -> Path:
    candidates: list[Path] = []
    resolved = shutil.which(_SCRIPT_NAME)
    if resolved:
        candidates.append(Path(resolved))
    candidates.extend(
        [
            Path(sysconfig.get_path("scripts")) / _SCRIPT_NAME,
            Path(sys.executable).with_name(_SCRIPT_NAME),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    pytest.skip("installed opensre executable is unavailable in this environment")
    raise AssertionError("pytest.skip should have interrupted control flow")


def _cli_env(home: Path, project_env_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in _CLEARED_ENV_KEYS:
        env.pop(key, None)

    existing_pythonpath = env.get("PYTHONPATH", "")
    pythonpath_parts = [str(REPO_ROOT)]
    user_site = site.getusersitepackages()
    if user_site:
        pythonpath_parts.append(user_site)
    if existing_pythonpath:
        pythonpath_parts.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["OPENSRE_NO_TELEMETRY"] = "1"
    env["OPENSRE_PROJECT_ENV_PATH"] = str(project_env_path)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["TERM"] = "xterm-256color"
    env.pop("OPENSRE_DISABLE_KEYRING", None)
    env["PYTHON_KEYRING_BACKEND"] = "tests.shared.keyring_backend.MemoryKeyring"
    return env


@pytest.fixture()
def cli_sandbox(tmp_path: Path) -> CliSandbox:
    home = tmp_path / "home"
    home.mkdir()
    project_env_path = tmp_path / "project.env"
    return CliSandbox(
        home=home,
        project_env_path=project_env_path,
        env=_cli_env(home, project_env_path),
    )


def _run_cli(
    sandbox: CliSandbox,
    *args: str,
    timeout: float = 15.0,
    extra_env: dict[str, str] | None = None,
) -> CliResult:
    executable = _opensre_executable()
    command = [str(executable), *args]
    if executable.suffix != ".exe":
        command = [sys.executable, str(executable), *args]

    env = sandbox.env.copy()
    if extra_env:
        env.update(extra_env)

    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return CliResult(
        args=tuple(args),
        exit_code=int(completed.returncode),
        stdout=_clean_terminal_output(completed.stdout),
        stderr=_clean_terminal_output(completed.stderr),
    )


def _read_pty_chunk(master_fd: int, timeout: float) -> bytes:
    ready, _, _ = select.select([master_fd], [], [], timeout)
    if not ready:
        return b""
    try:
        return os.read(master_fd, 4096)
    except OSError as exc:
        if exc.errno == errno.EIO:
            return b""
        raise


def _wait_for_output(
    process: subprocess.Popen[bytes],
    master_fd: int,
    buffer: bytearray,
    expected: str,
    *,
    timeout: float,
) -> None:
    deadline = time.monotonic() + timeout
    while expected not in _clean_terminal_output(buffer.decode("utf-8", errors="replace")):
        if time.monotonic() > deadline:
            cleaned = _clean_terminal_output(buffer.decode("utf-8", errors="replace"))
            raise AssertionError(f"Timed out waiting for {expected!r}.\nCurrent output:\n{cleaned}")
        chunk = _read_pty_chunk(master_fd, 0.1)
        if chunk:
            buffer.extend(chunk)
            continue
        if process.poll() is not None:
            break

    cleaned = _clean_terminal_output(buffer.decode("utf-8", errors="replace"))
    if expected not in cleaned:
        raise AssertionError(
            f"Process exited before showing {expected!r}.\nCurrent output:\n{cleaned}"
        )


def _run_cli_pty(
    sandbox: CliSandbox,
    *args: str,
    actions: list[PtyAction],
    timeout: float = 20.0,
    extra_env: dict[str, str] | None = None,
) -> CliResult:
    executable = _opensre_executable()
    command = [str(executable), *args]
    if executable.suffix != ".exe":
        command = [sys.executable, str(executable), *args]

    master_fd, slave_fd = os.openpty()
    env = sandbox.env.copy()
    if extra_env:
        env.update(extra_env)

    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        env=env,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    buffer = bytearray()
    try:
        for action in actions:
            _wait_for_output(process, master_fd, buffer, action.expect, timeout=action.timeout)
            os.write(master_fd, action.send)

        deadline = time.monotonic() + timeout
        while True:
            chunk = _read_pty_chunk(master_fd, 0.1)
            if chunk:
                buffer.extend(chunk)
                continue
            if process.poll() is not None:
                break
            if time.monotonic() > deadline:
                process.kill()
                cleaned = _clean_terminal_output(buffer.decode("utf-8", errors="replace"))
                raise AssertionError(f"Timed out waiting for CLI exit.\nCurrent output:\n{cleaned}")

        for _ in range(5):
            chunk = _read_pty_chunk(master_fd, 0.05)
            if not chunk:
                break
            buffer.extend(chunk)
    finally:
        os.close(master_fd)

    return CliResult(
        args=tuple(args),
        exit_code=int(process.wait(timeout=2.0)),
        stdout=_clean_terminal_output(buffer.decode("utf-8", errors="replace")),
    )


class _ReleaseHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        payload = json.dumps({"tag_name": "v9999.0.0"}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture()
def release_api_url() -> str:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ReleaseHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/releases/latest"
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()


def test_opensre_landing_page_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox)

    assert result.exit_code == 0
    assert "Quick start:" in result.stdout
    assert "opensre investigate -i alert.json" in result.stdout


def test_opensre_help_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "-h")

    assert result.exit_code == 0
    assert "Commands:" in result.stdout
    assert "integrations" in result.stdout
    assert "update" in result.stdout


def test_opensre_version_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "--version")

    assert result.exit_code == 0
    assert get_version() in result.stdout


def test_health_smoke_uses_real_datadog_store_config(cli_sandbox: CliSandbox) -> None:
    cli_sandbox.seed_integrations(
        [
            {
                "id": "datadog-local",
                "service": "datadog",
                "status": "active",
                "credentials": {
                    "api_key": "",
                    "app_key": "",
                    "site": "datadoghq.com",
                },
            }
        ]
    )

    result = _run_cli(cli_sandbox, "health")

    assert result.exit_code == 1
    assert "OpenSRE Health" in result.stdout
    assert "datadog" in result.stdout
    assert "Missing API key or application key." in result.stdout


def test_update_check_smoke_uses_local_stub(cli_sandbox: CliSandbox, release_api_url: str) -> None:
    result = _run_cli(
        cli_sandbox,
        "update",
        "--check",
        extra_env={"OPENSRE_RELEASES_API_URL": release_api_url},
    )

    assert result.exit_code == 1
    assert "current:" in result.stdout
    assert "latest:" in result.stdout
    assert "9999.0.0" in result.stdout


def test_investigate_print_template_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "investigate", "--print-template", "generic")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["alert_source"] == "generic"
    assert payload["message"]


def test_integrations_list_and_show_smoke(cli_sandbox: CliSandbox) -> None:
    cli_sandbox.seed_integrations(
        [
            {
                "id": "datadog-local",
                "service": "datadog",
                "status": "active",
                "credentials": {
                    "api_key": "dd-api-key",
                    "app_key": "dd-app-key",
                    "site": "datadoghq.com",
                },
            }
        ]
    )

    list_result = _run_cli(cli_sandbox, "integrations", "list")
    show_result = _run_cli(cli_sandbox, "integrations", "show", "datadog")

    assert list_result.exit_code == 0
    assert "datadog" in list_result.stdout
    assert "datadog-local" in list_result.stdout

    assert show_result.exit_code == 0
    assert '"service": "datadog"' in show_result.stdout
    assert '"api_key": "dd-a****"' in show_result.stdout
    assert '"app_key": "dd-a****"' in show_result.stdout


def test_integrations_verify_datadog_smoke(cli_sandbox: CliSandbox) -> None:
    cli_sandbox.seed_integrations(
        [
            {
                "id": "datadog-local",
                "service": "datadog",
                "status": "active",
                "credentials": {
                    "api_key": "",
                    "app_key": "",
                    "site": "datadoghq.com",
                },
            }
        ]
    )

    result = _run_cli(cli_sandbox, "integrations", "verify", "datadog")

    assert result.exit_code == 1
    assert "datadog" in result.stdout
    assert "Missing API key or application key." in result.stdout


def test_tests_inventory_commands_smoke(cli_sandbox: CliSandbox) -> None:
    list_result = _run_cli(cli_sandbox, "tests", "list", "--category", "ci-safe")
    run_result = _run_cli(cli_sandbox, "tests", "run", "make:test-cov", "--dry-run")

    assert list_result.exit_code == 0
    assert "make:test-cov" in list_result.stdout
    assert "make:test-full" in list_result.stdout

    assert run_result.exit_code == 0
    assert "make test-cov" in run_result.stdout


@pytest.mark.skipif(os.name == "nt", reason="interactive smoke uses POSIX PTYs")
def test_onboard_interactive_smoke(cli_sandbox: CliSandbox) -> None:
    # One `j` per keypress (burst writes are not separate keys). The select list wraps;
    # from the first option, len(choices)-1 steps reach "Skip for now" without wrapping past it.
    # 18 integrations + "Skip for now" = 19 choices → 18 j's from the top.
    result = _run_cli_pty(
        cli_sandbox,
        "onboard",
        actions=[
            PtyAction(expect="How do you want to get started?", send=b"\r"),
            PtyAction(expect="Choose your LLM provider", send=b"\r"),
            PtyAction(expect="Anthropic API key", send=b"smoke-test-key\r"),
            PtyAction(expect="Choose an integration to configure", send=b"jjjjjjjjjjjjjjjjjj\r"),
        ],
        timeout=30.0,
    )

    assert result.exit_code == 0
    assert "Done." in result.stdout
    assert "summary" in result.stdout

    store = cli_sandbox.read_wizard_store()
    assert store["targets"]["local"]["provider"] == "anthropic"
    assert "api_key" not in store["targets"]["local"]
    assert "LLM_PROVIDER=anthropic" in cli_sandbox.read_project_env()
    assert "ANTHROPIC_API_KEY=" not in cli_sandbox.read_project_env()
    assert "ANTHROPIC_REASONING_MODEL=" in cli_sandbox.read_project_env()


@pytest.mark.skipif(os.name == "nt", reason="interactive smoke uses POSIX PTYs")
def test_integrations_setup_datadog_interactive_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli_pty(
        cli_sandbox,
        "integrations",
        "setup",
        "datadog",
        actions=[
            PtyAction(expect="API key", send=b"dd-api-key\r"),
            PtyAction(expect="Application key", send=b"dd-app-key\r"),
            PtyAction(expect="Site", send=b"\r"),
        ],
    )

    assert "Saved" in result.stdout
    # Setup saves credentials then runs verify; placeholder keys fail the Datadog API check.
    assert result.exit_code in (0, 1)

    integrations = cli_sandbox.read_integrations()
    assert len(integrations) == 1
    assert integrations[0]["service"] == "datadog"
    # v2 store shape: credentials live inside the default instance.
    assert integrations[0]["instances"][0]["credentials"]["site"] == "datadoghq.com"


@pytest.mark.skipif(os.name == "nt", reason="interactive smoke uses POSIX PTYs")
def test_integrations_remove_datadog_interactive_smoke(cli_sandbox: CliSandbox) -> None:
    cli_sandbox.seed_integrations(
        [
            {
                "id": "datadog-local",
                "service": "datadog",
                "status": "active",
                "credentials": {
                    "api_key": "dd-api-key",
                    "app_key": "dd-app-key",
                    "site": "datadoghq.com",
                },
            }
        ]
    )

    result = _run_cli_pty(
        cli_sandbox,
        "integrations",
        "remove",
        "datadog",
        actions=[PtyAction(expect="Remove 'datadog'?", send=b"y\r")],
    )

    assert result.exit_code == 0
    assert "Removed 'datadog'." in result.stdout
    assert cli_sandbox.read_integrations() == []


@pytest.mark.skipif(os.name == "nt", reason="interactive smoke uses POSIX PTYs")
def test_tests_interactive_launcher_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli_pty(
        cli_sandbox,
        "tests",
        actions=[PtyAction(expect="Choose a test category:", send=b"\x03")],
    )

    assert result.exit_code == 0
    assert "Choose a test category:" in result.stdout


def test_deploy_help_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "deploy", "-h")

    assert result.exit_code == 0
    assert "ec2" in result.stdout
    assert "langsmith" in result.stdout
