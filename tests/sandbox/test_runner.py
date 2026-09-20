"""Tests for the Python sandbox runner."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from config.constants import OPENSRE_TMP_DIR, ensure_opensre_tmp_dir
from infrastructure.safety.sandbox.runner import (
    MAX_TIMEOUT,
    SandboxResult,
    python_interpreter_available,
    run_python_sandbox,
)


class TestSandboxRunnerBasicExecution:
    def test_runs_simple_code(self) -> None:
        result = run_python_sandbox("print('hello')")
        assert result.success
        assert result.stdout.strip() == "hello"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert not result.timed_out

    def test_captures_stderr(self) -> None:
        result = run_python_sandbox("import sys; sys.stderr.write('err\\n')")
        assert result.success
        assert "err" in result.stderr

    def test_captures_exit_code_on_failure(self) -> None:
        result = run_python_sandbox("raise ValueError('boom')")
        assert not result.success
        assert result.exit_code != 0
        assert "ValueError" in result.stderr

    def test_empty_code_succeeds(self) -> None:
        result = run_python_sandbox("")
        assert result.success
        assert result.stdout == ""

    def test_result_stores_original_code(self) -> None:
        code = "x = 1 + 1\nprint(x)"
        result = run_python_sandbox(code)
        assert result.code == code

    def test_result_stores_inputs(self) -> None:
        inputs = {"threshold": 42}
        result = run_python_sandbox("print(inputs['threshold'])", inputs=inputs)
        assert result.success
        assert result.inputs == inputs
        assert "42" in result.stdout

    def test_no_inputs_stores_empty_dict(self) -> None:
        result = run_python_sandbox("pass")
        assert result.inputs == {}

    def test_frozen_runner_skips_broken_candidate_and_uses_path_python(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frozen_executable = str(tmp_path / "opensre")
        bin_dir = tmp_path / "bin"
        broken_python = str(bin_dir / "python3")
        python_executable = str(bin_dir / "python")
        commands: list[list[str]] = []

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", frozen_executable)
        monkeypatch.setenv("PATH", str(bin_dir))
        monkeypatch.setattr(
            "infrastructure.safety.sandbox.runner.OPENSRE_TMP_DIR",
            tmp_path,
        )
        monkeypatch.setattr(
            "infrastructure.safety.sandbox.runner.ensure_opensre_tmp_dir",
            lambda: None,
        )

        def _which(command: str, **_kwargs: Any) -> str | None:
            return {
                "python3": broken_python,
                "python": python_executable,
            }.get(Path(command).name)

        monkeypatch.setattr(shutil, "which", _which)

        def _run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            if command[0] == broken_python:
                return subprocess.CompletedProcess(command, 1, stdout="", stderr="broken")
            return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)

        result = run_python_sandbox("print('ok')")

        assert result.success is True
        assert python_interpreter_available() is True
        assert [command[0] for command in commands] == [
            broken_python,
            python_executable,
            python_executable,
        ]
        assert commands[1][1:3] == ["-I", "-c"]
        assert commands[2][0] != frozen_executable
        assert commands[2][1] == "-I"
        assert Path(commands[2][2]).suffix == ".py"

    def test_frozen_interpreter_unavailable_when_all_candidates_are_broken(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frozen_executable = str(tmp_path / "opensre")
        bin_dir = tmp_path / "bin"
        candidates = {
            "python3": str(bin_dir / "python3"),
            "python": str(bin_dir / "python"),
        }
        commands: list[list[str]] = []

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", frozen_executable)
        monkeypatch.setenv("PATH", str(bin_dir))

        def _which(command: str, **_kwargs: Any) -> str | None:
            return candidates.get(Path(command).name)

        monkeypatch.setattr(shutil, "which", _which)

        def _run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="broken")

        monkeypatch.setattr(subprocess, "run", _run)

        assert python_interpreter_available() is False
        assert [command[0] for command in commands] == list(candidates.values())

    def test_frozen_runner_ignores_implicit_current_directory_python(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frozen_executable = str(tmp_path / "opensre")
        implicit_python = str(tmp_path / "python3")
        bin_dir = tmp_path / "safe-bin"
        path_python = str(bin_dir / "python3")
        commands: list[list[str]] = []

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", frozen_executable)
        monkeypatch.setenv("PATH", str(bin_dir))
        monkeypatch.setattr(
            "infrastructure.safety.sandbox.runner.OPENSRE_TMP_DIR",
            tmp_path,
        )
        monkeypatch.setattr(
            "infrastructure.safety.sandbox.runner.ensure_opensre_tmp_dir",
            lambda: None,
        )

        def _which(command: str, **_kwargs: Any) -> str | None:
            command_path = Path(command)
            if not command_path.is_absolute():
                return implicit_python
            if command_path.parent == bin_dir:
                return path_python
            return None

        def _run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

        monkeypatch.setattr(shutil, "which", _which)
        monkeypatch.setattr(subprocess, "run", _run)

        result = run_python_sandbox("print('ok')")

        assert result.success is True
        assert [command[0] for command in commands] == [path_python, path_python]
        assert implicit_python not in {command[0] for command in commands}

    def test_frozen_runner_without_path_python_does_not_spawn_opensre(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        frozen_executable = str(tmp_path / "opensre")
        commands: list[list[str]] = []

        monkeypatch.setattr(sys, "frozen", True, raising=False)
        monkeypatch.setattr(sys, "executable", frozen_executable)
        monkeypatch.setenv("PATH", str(tmp_path))
        monkeypatch.setattr(
            "infrastructure.safety.sandbox.runner.OPENSRE_TMP_DIR",
            tmp_path,
        )
        monkeypatch.setattr(
            "infrastructure.safety.sandbox.runner.ensure_opensre_tmp_dir",
            lambda: None,
        )

        def _which(command: str, **_kwargs: Any) -> str | None:
            return frozen_executable if Path(command).name == "python3" else None

        monkeypatch.setattr(shutil, "which", _which)

        def _run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", _run)

        result = run_python_sandbox("print('never reached')")

        assert commands == []
        assert result.success is False
        assert result.exit_code == -1
        assert result.timed_out is False
        assert result.error is not None
        assert "Python" in result.error


class TestSandboxRunnerInputInjection:
    def test_inputs_injected_as_variable(self) -> None:
        code = "print(inputs['key'])"
        result = run_python_sandbox(code, inputs={"key": "value123"})
        assert result.success
        assert "value123" in result.stdout

    def test_inputs_supports_nested_structures(self) -> None:
        code = "print(inputs['data'][0])"
        result = run_python_sandbox(code, inputs={"data": [99, 100]})
        assert result.success
        assert "99" in result.stdout

    def test_none_inputs_not_injected(self) -> None:
        result = run_python_sandbox("x = 1", inputs=None)
        assert result.success
        assert result.inputs == {}


class TestSandboxNetworkRestrictions:
    def test_socket_creation_blocked(self) -> None:
        code = "import socket; socket.socket()"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout

    def test_create_connection_blocked(self) -> None:
        code = "import socket; socket.create_connection(('localhost', 80))"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout

    def test_getaddrinfo_blocked(self) -> None:
        code = "import socket; socket.getaddrinfo('localhost', 80)"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout


class TestSandboxFilesystemRestrictions:
    def test_read_allowed(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("data")
            path = f.name
        try:
            result = run_python_sandbox(f"open({path!r}).read()")
            assert result.success
        finally:
            os.unlink(path)

    def test_write_outside_tmp_blocked(self) -> None:
        code = "open('/etc/sandbox_test_file', 'w').write('x')"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout

    def test_write_inside_opensre_tmp_allowed(self) -> None:
        ensure_opensre_tmp_dir()
        target = os.path.join(os.fspath(OPENSRE_TMP_DIR), "sandbox_write_test.txt")
        code = f"open({target!r}, 'w').write('ok')"
        result = run_python_sandbox(code)
        assert result.success
        if os.path.exists(target):
            os.unlink(target)

    def test_append_outside_tmp_blocked(self) -> None:
        code = "open('/etc/sandbox_append_test', 'a').write('x')"
        result = run_python_sandbox(code)
        assert not result.success
        assert "PermissionError" in result.stderr or "PermissionError" in result.stdout

    def test_restricted_open_windows_paths_are_normalized(self) -> None:
        ensure_opensre_tmp_dir()
        target = os.path.join(os.fspath(OPENSRE_TMP_DIR), "sandbox_win_test.txt")
        test_path = target.upper() if os.name == "nt" else target
        test_path = test_path.replace("/", "\\") if os.name == "nt" else test_path
        
        code = f"open({test_path!r}, 'w').write('ok')"
        result = run_python_sandbox(code)
        assert result.success
        
        if os.path.exists(test_path):
            os.unlink(test_path)

    def test_restricted_open_bytes_paths(self) -> None:
        ensure_opensre_tmp_dir()
        target = os.path.join(os.fspath(OPENSRE_TMP_DIR), "sandbox_bytes_test.txt")
        code = f"open({target.encode('utf-8')!r}, 'w').write('ok')"
        result = run_python_sandbox(code)
        assert result.success
        if os.path.exists(target):
            os.unlink(target)


class TestSandboxTimeout:
    def test_timeout_enforced(self) -> None:
        result = run_python_sandbox("import time; time.sleep(10)", timeout=1)
        assert not result.success
        assert result.timed_out
        assert result.exit_code == -1
        assert result.error is not None
        assert "timed out" in result.error.lower()

    def test_timeout_capped_at_max(self) -> None:
        result = run_python_sandbox("pass", timeout=MAX_TIMEOUT + 9999)
        assert result.success

    def test_fast_code_does_not_time_out(self) -> None:
        result = run_python_sandbox("print('fast')", timeout=30)
        assert result.success
        assert not result.timed_out


class TestSandboxResultModel:
    def test_success_property_true_on_zero_exit(self) -> None:
        r = SandboxResult(
            code="",
            inputs={},
            stdout="",
            stderr="",
            exit_code=0,
            timed_out=False,
        )
        assert r.success is True

    def test_success_property_false_on_nonzero_exit(self) -> None:
        r = SandboxResult(
            code="",
            inputs={},
            stdout="",
            stderr="",
            exit_code=1,
            timed_out=False,
        )
        assert r.success is False

    def test_success_property_false_when_timed_out(self) -> None:
        r = SandboxResult(
            code="",
            inputs={},
            stdout="",
            stderr="",
            exit_code=0,
            timed_out=True,
        )
        assert r.success is False
