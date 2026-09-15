from __future__ import annotations

import contextlib
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
from collections.abc import Iterator
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from config.constants.paths import REPO_ROOT
from config.version import get_opensre_version
from tests.utils.polling import wait_until

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
    "GITHUB_MCP_AUTH_TOKEN",
    "GITHUB_MCP_MODE",
    "GITHUB_MCP_TOOLSETS",
    "GITHUB_MCP_URL",
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
    "OPENSRE_LLM_AUTH_METADATA_PATH",
    "OPENSRE_PROJECT_ENV_PATH",
    "OPENSRE_RELEASES_API_URL",
    "OPENSRE_WIZARD_STORE_PATH",
    "SLACK_WEBHOOK_URL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_DEFAULT_CHAT_ID",
    "TRACER_API_URL",
    "TRACER_WEB_APP_URL",
    "TRUSTEDROUTER_API_KEY",
    "X_BEARER_TOKEN",
    "X_MCP_ARGS",
    "X_MCP_AUTH_TOKEN",
    "X_MCP_COMMAND",
    "X_MCP_MODE",
    "X_MCP_URL",
)


@dataclass(frozen=True)
class CliResult:
    args: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str = ""


@dataclass(frozen=True)
class PtyAction:
    #: Wait until this substring appears, or any substring if a tuple is given.
    expect: str | tuple[str, ...]
    send: bytes
    timeout: float = 10.0
    #: If > 0, send this many ``stagger_key`` keypresses one at a time
    #: (prompt_toolkit may coalesce a single burst), then send ``send``
    #: (usually ``\\r``).
    stagger_j: int = 0
    #: The single-keypress navigation byte sent ``stagger_j`` times. Defaults to
    #: ``j`` (vim-down). Use ``k`` (vim-up) to reach the last option in one step,
    #: since questionary select menus wrap around.
    stagger_key: bytes = b"j"


@dataclass
class CliSandbox:
    home: Path
    project_env_path: Path
    env: dict[str, str]

    @property
    def integration_store_path(self) -> Path:
        return self.home / ".opensre" / "integrations.json"

    @property
    def wizard_store_path(self) -> Path:
        return self.home / ".opensre" / "opensre.json"

    def seed_integrations(self, integrations: list[dict[str, object]]) -> None:
        self.integration_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.integration_store_path.write_text(
            json.dumps({"version": 1, "integrations": integrations}, indent=2) + "\n",
            encoding="utf-8",
        )

    def seed_wizard_store(
        self,
        *,
        provider: str = "anthropic",
        model: str = "claude-opus-4-7",
    ) -> None:
        self.wizard_store_path.parent.mkdir(parents=True, exist_ok=True)
        self.wizard_store_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "wizard": {
                        "mode": "quickstart",
                        "configured_target": "local",
                        "updated_at": "2026-01-01T00:00:00+00:00",
                    },
                    "targets": {
                        "local": {
                            "provider": provider,
                            "model": model,
                            "api_key_env": f"{provider.upper()}_API_KEY",
                            "model_env": f"{provider.upper()}_REASONING_MODEL",
                            "updated_at": "2026-01-01T00:00:00+00:00",
                        }
                    },
                    "probes": {"local": {}, "remote": {}},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    def seed_project_env(
        self,
        *,
        provider: str = "anthropic",
        model: str = "claude-opus-4-7",
    ) -> None:
        model_env = f"{provider.upper()}_REASONING_MODEL"
        self.project_env_path.write_text(
            f"LLM_PROVIDER={provider}\n{model_env}={model}\n",
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
        store: dict[str, object] = json.loads(self.wizard_store_path.read_text(encoding="utf-8"))
        return store

    def wizard_target(self, name: str) -> dict[str, object]:
        """Return the wizard store's entry for one target (e.g. ``local``)."""
        targets = self.read_wizard_store()["targets"]
        assert isinstance(targets, dict), f"wizard store 'targets' is not a mapping: {targets!r}"
        entry = targets[name]
        assert isinstance(entry, dict), f"wizard target {name!r} is not a mapping: {entry!r}"
        return entry


def _clean_terminal_output(text: str) -> str:
    if not text:
        return ""
    cleaned = _ANSI_RE.sub("", text)
    cleaned = cleaned.replace("\r", "\n").replace("\x00", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _opensre_executable() -> Path:
    """Return the ``opensre`` entrypoint from the active test interpreter's venv.

    Prefer the script adjacent to ``sys.executable`` over ``shutil.which`` so CI
    and local xdist workers never invoke a stale global ``opensre`` on ``PATH``
    that predates the ``remote`` command (smoke tests would otherwise see
    ``Error: No such command 'remote'``).
    """
    candidates: list[Path] = [
        Path(sys.executable).with_name(_SCRIPT_NAME),
        Path(sysconfig.get_path("scripts")) / _SCRIPT_NAME,
    ]
    resolved = shutil.which(_SCRIPT_NAME)
    if resolved:
        candidates.append(Path(resolved))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    pytest.skip("installed opensre executable is unavailable in this environment")
    raise AssertionError("pytest.skip should have interrupted control flow")


def _is_python_script(path: Path) -> bool:
    """Return True when an executable should be launched via Python."""
    if path.suffix in {".py", ".pyw"}:
        return True
    try:
        first_line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return False
    return first_line.startswith("#!") and "python" in first_line.lower()


def _cli_env(home: Path, project_env_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    # Blank values block ``load_dotenv(override=False)`` from re-importing the repo
    # ``.env`` when subprocesses run with ``cwd=REPO_ROOT``.
    for key in _CLEARED_ENV_KEYS:
        env[key] = ""

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
    # Pin the product home into the sandbox. A parent ``OPENSRE_HOME`` (common
    # for local workbench / release smoke) would otherwise leak real
    # integrations and LLM credentials into these subprocesses.
    env["OPENSRE_HOME"] = str(home / ".opensre")
    env["OPENSRE_NO_TELEMETRY"] = "1"
    env["OPENSRE_PROJECT_ENV_PATH"] = str(project_env_path)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["TERM"] = "xterm-256color"
    env.pop("OPENSRE_DISABLE_KEYRING", None)
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
    if executable.suffix != ".exe" and _is_python_script(executable):
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
    expected: str | tuple[str, ...],
    *,
    timeout: float,
) -> None:
    def _matches(cleaned: str) -> bool:
        if isinstance(expected, str):
            return expected in cleaned
        return any(sub in cleaned for sub in expected)

    deadline = time.monotonic() + timeout
    while not _matches(_clean_terminal_output(buffer.decode("utf-8", errors="replace"))):
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
    if not _matches(cleaned):
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
    if executable.suffix != ".exe" and _is_python_script(executable):
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
            if action.stagger_j:
                for _ in range(action.stagger_j):
                    os.write(master_fd, action.stagger_key)
                    # Pace keystrokes so prompt_toolkit doesn't coalesce a
                    # burst; there's no observable condition to poll for
                    # here, so wait_until always times out by design.
                    with contextlib.suppress(TimeoutError):
                        wait_until(lambda: False, timeout=0.05, interval=0.05)
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
    def do_GET(self) -> None:
        payload = json.dumps(
            {
                "tag_name": "main-build",
                "body": "- Version: `9999.0.0`\n- Commit: `deadbeef`\n",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture()
def release_api_url() -> Iterator[str]:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ReleaseHandler)
    except OSError as exc:
        if exc.errno in {errno.EPERM, errno.EACCES}:
            pytest.skip("localhost HTTP server binding is not permitted in this environment")
        raise

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/releases/tags/main-build"
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()


def test_opensre_landing_page_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox)

    assert result.exit_code == 0
    assert "Quick start:" in result.stdout
    assert "opensre ask" in result.stdout


def test_opensre_help_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "-h")

    assert result.exit_code == 0
    assert "Welcome back" not in result.stdout
    # Commands are grouped so the entry point is not buried alphabetically.
    assert "Getting started:" in result.stdout
    assert "setup" in result.stdout
    assert "onboard" in result.stdout
    assert "integrations" in result.stdout
    assert "--interactive / --no-interactive" in result.stdout
    assert "--layout [classic|pinned]" in result.stdout
    assert "update" in result.stdout


def test_opensre_version_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "--version")

    assert result.exit_code == 0
    assert get_opensre_version() in result.stdout


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

    assert result.exit_code == 0
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


def test_integrations_show_and_remove_retired_service_smoke(
    cli_sandbox: CliSandbox,
) -> None:
    service = "retired-observer"
    cli_sandbox.seed_integrations(
        [
            {
                "id": "retired-local",
                "service": service,
                "status": "active",
                "credentials": {"api_key": "retired-secret"},
            }
        ]
    )

    show_result = _run_cli(cli_sandbox, "integrations", "show", service)
    remove_result = _run_cli(
        cli_sandbox,
        "--yes",
        "integrations",
        "remove",
        service,
    )
    list_result = _run_cli(cli_sandbox, "integrations", "list")

    assert show_result.exit_code == 0
    assert f'"service": "{service}"' in show_result.stdout
    assert '"api_key": "reti****"' in show_result.stdout
    assert remove_result.exit_code == 0
    assert f"Removed '{service}'." in remove_result.stdout
    assert list_result.exit_code == 0
    assert "No integrations." in list_result.stdout


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


@pytest.fixture()
def rejected_provider_endpoint() -> Iterator[tuple[str, list[str]]]:
    """Exercise credential rejection without sending the probe to a provider."""
    requests: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_CONNECT(self) -> None:
            requests.append(self.path)
            self.send_error(HTTPStatus.FORBIDDEN, "Provider connections blocked by test")

        def do_POST(self) -> None:
            requests.append(self.path)
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            payload = b'{"error":{"message":"Invalid test key","type":"authentication_error"}}'
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(os.name == "nt", reason="interactive smoke uses POSIX PTYs")
def test_onboard_interactive_smoke(
    cli_sandbox: CliSandbox, rejected_provider_endpoint: tuple[str, list[str]]
) -> None:
    probe_url, probe_requests = rejected_provider_endpoint
    result = _run_cli_pty(
        cli_sandbox,
        "onboard",
        actions=[
            PtyAction(expect="Choose your LLM provider", send=b"\r"),
            # #3591: the model is picked BEFORE the credential, so the live probe runs
            # against the model that actually gets persisted.
            PtyAction(expect="Choose OpenAI model", send=b"\r"),
            PtyAction(expect="OpenAI API key", send=b"smoke-test-key\r"),
            # The local probe returns 401; choose "Save anyway without validating"
            # to exercise credential persistence after an explicit rejection.
            PtyAction(
                expect="could not be verified. What next?",
                send=b"\r",
                stagger_j=1,
            ),
        ],
        timeout=30.0,
        extra_env={
            "OPENSRE_AUTO_LAUNCH": "0",
            "OPENAI_BASE_URL": probe_url,
        },
    )

    assert result.exit_code == 0
    assert probe_requests
    assert "Done." in result.stdout
    assert "next" in result.stdout

    target = cli_sandbox.wizard_target("local")
    assert target["provider"] == "openai"
    assert "api_key" not in target
    assert "LLM_PROVIDER=openai" in cli_sandbox.read_project_env()
    assert "OPENAI_API_KEY=" in cli_sandbox.read_project_env()
    assert "OPENAI_REASONING_MODEL=" in cli_sandbox.read_project_env()


@pytest.mark.parametrize(
    ("_cli_binary", "provider_key", "provider_label", "pty_timeout"),
    [
        pytest.param(
            "opencode",
            "opencode",
            "OpenCode CLI",
            120.0,
            marks=pytest.mark.skipif(
                shutil.which("opencode") is None,
                reason="OpenCode CLI not on PATH",
            ),
        ),
    ],
)
@pytest.mark.skipif(os.name == "nt", reason="interactive smoke uses POSIX PTYs")
def test_onboard_interactive_smoke_cli_provider_repick_when_unauthenticated(
    cli_sandbox: CliSandbox,
    rejected_provider_endpoint: tuple[str, list[str]],
    _cli_binary: str,
    provider_key: str,
    provider_label: str,
    pty_timeout: float,
) -> None:
    """PTY: local CLI LLM → repick when unauthenticated, then finish as OpenAI.

    Navigates through the first-menu ``Other`` branch for less common providers.
    Fresh HOME has no CLI auth, so either ``requires login`` or ``Could not verify … login``
    is accepted before choosing repick. Skips when the CLI binary for each parametrized
    case is not on PATH.
    """
    from surfaces.cli.wizard.custom_endpoints import CUSTOM_ENDPOINT_SELECTION
    from surfaces.shared.llm_setup.provider_choices import other_setup_provider_options

    probe_url, probe_requests = rejected_provider_endpoint
    other_values = [
        CUSTOM_ENDPOINT_SELECTION,
        *(
            provider.value
            for provider in other_setup_provider_options()
            if provider.value not in {"custom-openai", "custom-anthropic"}
        ),
    ]
    other_index = other_values.index(provider_key) if provider_key in other_values else -1
    other_default_index = other_values.index("anthropic")
    other_stagger_up = (other_default_index - other_index) % len(other_values)
    assert other_index >= 0, f"Provider '{provider_key}' missing from onboarding providers"
    assert other_stagger_up > 0, (
        f"Provider '{provider_key}' is already the default in the other-provider menu"
    )

    login_prompt: tuple[str, ...] = (
        f"{provider_label} requires login. What next?",
        f"Could not verify {provider_label} login. What next?",
    )
    model_prompt: tuple[str, ...] = (
        f"Choose {provider_label} model",
        "Model",
    )
    actions = [
        PtyAction(expect="Choose your LLM provider", send=b"\r", stagger_j=2),
        PtyAction(
            expect="Choose another LLM provider",
            send=b"\r",
            stagger_j=other_stagger_up,
            stagger_key=b"k",
        ),
        PtyAction(expect=model_prompt, send=b"\r", timeout=30.0),
        PtyAction(
            expect=login_prompt,
            send=b"\r",
            stagger_j=1,
            timeout=90.0,
        ),
        PtyAction(expect="Choose your LLM provider", send=b"\r"),
        # #3591: the model is chosen BEFORE the credential, so the live probe
        # runs against the model that gets persisted. Same order as
        # test_onboard_interactive_smoke: model -> key -> recovery menu.
        PtyAction(expect="Choose OpenAI model", send=b"\r"),
        PtyAction(expect="OpenAI API key", send=b"smoke-test-key\r"),
        # smoke-test-key fails validation; move to "Save anyway without
        # validating" to keep the local credentials-file path and every
        # downstream assertion intact.
        PtyAction(
            expect="could not be verified. What next?",
            send=b"\r",
            stagger_j=1,
            timeout=90.0,
        ),
    ]

    try:
        result = _run_cli_pty(
            cli_sandbox,
            "onboard",
            actions=actions,
            timeout=pty_timeout,
            extra_env={
                "OPENSRE_AUTO_LAUNCH": "0",
                "OPENAI_API_KEY": "",
                "OPENAI_ORG_ID": "",
                "OPENAI_PROJECT_ID": "",
                "OPENAI_BASE_URL": probe_url,
            },
        )
    except AssertionError as exc:
        msg = str(exc)
        if (
            _cli_binary == "opencode"
            and "environment provider key(s)" in msg
            and "OpenCode:" in msg
        ):
            pytest.skip("OpenCode CLI is already authenticated via env; unauth repick flow skipped")
        raise

    assert result.exit_code == 0
    assert probe_requests
    assert "Done." in result.stdout
    assert "next" in result.stdout

    target = cli_sandbox.wizard_target("local")
    assert target["provider"] == "openai"
    assert "api_key" not in target
    env_body = cli_sandbox.read_project_env()
    assert "LLM_PROVIDER=openai\n" in env_body
    assert "OPENAI_API_KEY=" in env_body
    assert "OPENAI_REASONING_MODEL=" in env_body


@pytest.mark.skipif(os.name == "nt", reason="interactive smoke uses POSIX PTYs")
def test_integrations_setup_datadog_rejects_credentials_that_do_not_verify(
    cli_sandbox: CliSandbox,
    rejected_provider_endpoint: tuple[str, list[str]],
) -> None:
    """Placeholder keys must leave nothing behind, on any tier.

    This used to save first and verify afterwards, so a typo'd key overwrote a
    working integration and the command still reported ``Saved``. The shared
    setup flow verifies before it persists; with keys the Datadog API rejects,
    the store and ``.env`` are expected to stay untouched.
    """
    probe_url, probe_requests = rejected_provider_endpoint
    proxy_url = probe_url.removesuffix("/v1")
    result = _run_cli_pty(
        cli_sandbox,
        "integrations",
        "setup",
        "datadog",
        actions=[
            PtyAction(expect="API key", send=b"dd-api-key\r"),
            PtyAction(expect="application key", send=b"dd-app-key\r"),
            PtyAction(expect="Site", send=b"\r"),
        ],
        # The loopback proxy rejects CONNECT without forwarding data to Datadog.
        extra_env={
            "HTTPS_PROXY": proxy_url,
            "https_proxy": proxy_url,
            "NO_PROXY": "",
            "no_proxy": "",
        },
        timeout=45.0,
    )

    assert result.exit_code == 1
    assert probe_requests == ["api.datadoghq.com:443"]
    assert "Saved" not in result.stdout
    assert cli_sandbox.read_integrations() == []
    assert "DD_SITE" not in cli_sandbox.read_project_env()


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


def test_gateway_help_smoke(cli_sandbox: CliSandbox) -> None:
    result = _run_cli(cli_sandbox, "gateway", "-h")

    assert result.exit_code == 0
    assert "telegram" in result.stdout
