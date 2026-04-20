"""Tests for the run_diagnostic_code tool."""

from __future__ import annotations

from unittest.mock import patch

from app.sandbox.runner import SandboxResult
from app.tools.registered_tool import REGISTERED_TOOL_ATTR, RegisteredTool
from app.tools.run_diagnostic_code import run_diagnostic_code
from tests.tools.conftest import BaseToolContract


def _registered() -> RegisteredTool:
    r = getattr(run_diagnostic_code, REGISTERED_TOOL_ATTR, None)
    assert isinstance(r, RegisteredTool)
    return r


class TestRunDiagnosticCodeContract(BaseToolContract):
    def get_tool_under_test(self) -> RegisteredTool:
        return _registered()


class TestRunDiagnosticCodeMetadata:
    def test_tool_name(self) -> None:
        assert _registered().name == "run_diagnostic_code"

    def test_tool_source(self) -> None:
        assert _registered().source == "knowledge"

    def test_input_schema_has_code(self) -> None:
        props = _registered().input_schema["properties"]
        assert "code" in props

    def test_code_is_required(self) -> None:
        assert "code" in _registered().input_schema["required"]

    def test_inputs_and_timeout_are_optional(self) -> None:
        required = _registered().input_schema.get("required", [])
        assert "inputs" not in required
        assert "timeout" not in required

    def test_tool_is_available_without_sources(self) -> None:
        assert _registered().is_available({}) is True

    def test_registered_on_investigation_surface(self) -> None:
        assert "investigation" in _registered().surfaces


class TestRunDiagnosticCodeExecution:
    def test_successful_execution_returns_stdout(self) -> None:
        result = run_diagnostic_code(code="print('hello world')")
        assert result["success"] is True
        assert "hello world" in result["stdout"]
        assert result["exit_code"] == 0
        assert result["timed_out"] is False
        assert "error" not in result

    def test_result_includes_structured_evidence_fields(self) -> None:
        result = run_diagnostic_code(code="x = 1 + 1")
        assert "code" in result
        assert "inputs" in result
        assert "stdout" in result
        assert "stderr" in result
        assert "exit_code" in result
        assert "timed_out" in result
        assert "success" in result
        assert result["source"] == "knowledge"

    def test_result_stores_original_code(self) -> None:
        code = "print('test')"
        result = run_diagnostic_code(code=code)
        assert result["code"] == code

    def test_inputs_passed_to_sandbox(self) -> None:
        result = run_diagnostic_code(
            code="print(inputs['val'])",
            inputs={"val": "injected"},
        )
        assert result["success"] is True
        assert "injected" in result["stdout"]
        assert result["inputs"] == {"val": "injected"}

    def test_failure_returns_non_zero_exit_code(self) -> None:
        result = run_diagnostic_code(code="raise RuntimeError('fail')")
        assert result["success"] is False
        assert result["exit_code"] != 0

    def test_timeout_produces_timed_out_true(self) -> None:
        result = run_diagnostic_code(
            code="import time; time.sleep(10)",
            timeout=1,
        )
        assert result["timed_out"] is True
        assert result["success"] is False
        assert "error" in result

    def test_timeout_capped_at_max(self) -> None:
        with patch("app.tools.run_diagnostic_code.run_python_sandbox") as mock_run:
            mock_run.return_value = SandboxResult(
                code="pass",
                inputs={},
                stdout="",
                stderr="",
                exit_code=0,
                timed_out=False,
            )
            run_diagnostic_code(code="pass", timeout=9999)
            _, kwargs = mock_run.call_args
            assert kwargs["timeout"] <= 60

    def test_default_timeout_is_applied_when_none(self) -> None:
        with patch("app.tools.run_diagnostic_code.run_python_sandbox") as mock_run:
            mock_run.return_value = SandboxResult(
                code="pass",
                inputs={},
                stdout="",
                stderr="",
                exit_code=0,
                timed_out=False,
            )
            run_diagnostic_code(code="pass")
            _, kwargs = mock_run.call_args
            assert kwargs["timeout"] == 30

    def test_error_field_present_on_runner_error(self) -> None:
        with patch("app.tools.run_diagnostic_code.run_python_sandbox") as mock_run:
            mock_run.return_value = SandboxResult(
                code="pass",
                inputs={},
                stdout="",
                stderr="",
                exit_code=-1,
                timed_out=False,
                error="Something went wrong",
            )
            result = run_diagnostic_code(code="pass")
            assert result["error"] == "Something went wrong"
            assert result["success"] is False

    def test_no_error_field_on_success(self) -> None:
        result = run_diagnostic_code(code="pass")
        assert "error" not in result


class TestRunDiagnosticCodeSandboxRestrictions:
    def test_network_access_blocked(self) -> None:
        result = run_diagnostic_code(code="import socket; socket.socket()")
        assert result["success"] is False
        assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]

    def test_filesystem_write_outside_tmp_blocked(self) -> None:
        result = run_diagnostic_code(code="open('/etc/sandbox_diag_test', 'w').write('x')")
        assert result["success"] is False
        assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]
