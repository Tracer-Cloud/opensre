"""Tests for the Python sandbox runner."""

from __future__ import annotations

import os
import tempfile

from app.sandbox.runner import (
    MAX_TIMEOUT,
    SandboxResult,
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
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as f:
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

    def test_write_inside_tmp_allowed(self) -> None:
        tmp_dir = tempfile.gettempdir()
        target = os.path.join(tmp_dir, "sandbox_write_test.txt")
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
