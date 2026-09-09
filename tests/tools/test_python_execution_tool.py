"""Tests for the Python execution tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from infrastructure.safety.sandbox.runner import SandboxResult
from tests.tools.conftest import BaseToolContract
from tools.registry import clear_tool_registry_cache, get_registered_tool_map
from tools.system.python_execution_tool import execute_python_code
from tools.system.python_execution_tool._evidence import map_execute_python_code


class TestPythonExecutionToolContract(BaseToolContract):
    def get_tool_under_test(self) -> object:
        return execute_python_code


class TestPythonExecutionToolMetadata:
    def test_tool_name(self) -> None:
        assert execute_python_code.name == "execute_python_code"

    def test_registered_on_interactive_surfaces(self) -> None:
        clear_tool_registry_cache()
        registered = get_registered_tool_map("chat")["execute_python_code"]
        assert "chat" in registered.surfaces

    def test_not_advertised_without_a_python_interpreter(self, monkeypatch) -> None:
        def _unavailable() -> str:
            raise FileNotFoundError("Python 3 is not available on PATH")

        monkeypatch.setattr(
            "infrastructure.safety.sandbox.runner._python_executable",
            _unavailable,
        )

        clear_tool_registry_cache()
        registered = get_registered_tool_map("chat")["execute_python_code"]

        assert execute_python_code.is_available({}) is False
        assert registered.is_available({}) is False

    def test_guidance_does_not_require_bundled_dependencies(self) -> None:
        clear_tool_registry_cache()
        registered = get_registered_tool_map("chat")["execute_python_code"]
        guidance = " ".join(
            [
                registered.description,
                *registered.use_cases,
                *registered.anti_examples,
                registered.skill_guidance,
            ]
        )

        assert "psutil" not in guidance
        assert "opensre_runtime" in guidance

    def test_github_token_hidden_from_public_schema(self) -> None:
        clear_tool_registry_cache()
        registered = get_registered_tool_map("chat")["execute_python_code"]
        props = registered.public_input_schema["properties"]
        assert "github_token" not in props
        assert "github_token" in registered.injected_params

    def test_does_not_coach_unrestricted_reachability_probing(self) -> None:
        """allow_network is all-or-nothing with no destination allowlist.
        Model-facing tool metadata must not advertise socket.create_connection
        + allow_network as a reachability recipe — natural-language host
        limits are not a security boundary."""
        description = execute_python_code.description
        assert "socket.create_connection" not in description
        assert "reachability" not in description.lower()
        assert "approved API-backed analysis" in description
        anti = " ".join(execute_python_code.anti_examples)
        assert "arbitrary host/port reachability" in anti
        assert "socket.create_connection" not in " ".join(execute_python_code.use_cases)

    def test_github_star_velocity_skill_guidance_is_attached(self) -> None:
        clear_tool_registry_cache()
        registered = get_registered_tool_map("chat")["execute_python_code"]
        marker = "Stargazers are returned **oldest first**"
        assert "Workflow guidance:" in registered.description
        assert '<skill name="measuring-github-star-velocity"' in registered.skill_guidance
        assert marker in registered.skill_guidance
        assert marker in registered.description


class TestPythonExecutionToolExecution:
    def test_successful_execution_returns_stdout(self) -> None:
        result = execute_python_code.run(code="print('hello world')")
        assert result["success"] is True
        assert "hello world" in result["stdout"]
        assert result["stderr"] == ""
        assert result["exit_code"] == 0

    def test_inputs_are_injected(self) -> None:
        result = execute_python_code.run(
            code="print(inputs['owner'] + '/' + inputs['repo'])",
            inputs={"owner": "Tracer-Cloud", "repo": "opensre"},
        )
        assert result["success"] is True
        assert "Tracer-Cloud/opensre" in result["stdout"]
        assert result["inputs"]["owner"] == "Tracer-Cloud"
        assert result["inputs"]["repo"] == "opensre"
        assert "opensre_runtime" in result["inputs"]

    def test_failure_returns_non_zero_exit_code(self) -> None:
        result = execute_python_code.run(code="raise RuntimeError('boom')")
        assert result["success"] is False
        assert result["exit_code"] != 0
        assert "RuntimeError" in result["stderr"]

    def test_timeout_produces_timed_out_true(self) -> None:
        result = execute_python_code.run(code="import time; time.sleep(10)", timeout=1)
        assert result["success"] is False
        assert result["timed_out"] is True
        assert "error" in result

    def test_timeout_capped_at_max(self) -> None:
        with patch("tools.system.python_execution_tool.runner.run_python_sandbox") as mock_run:
            mock_run.return_value = SandboxResult(
                code="pass",
                inputs={},
                stdout="",
                stderr="",
                exit_code=0,
                timed_out=False,
            )
            execute_python_code.run(code="pass", timeout=9999)
            _, kwargs = mock_run.call_args
            assert kwargs["timeout"] <= 60


class TestPythonExecutionToolRestrictions:
    def test_network_access_blocked_by_default(self) -> None:
        result = execute_python_code.run(code="import socket; socket.socket()")
        assert result["success"] is False
        assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]

    def test_network_access_can_be_enabled(self) -> None:
        result = execute_python_code.run(
            code="import socket; s = socket.socket(); s.close(); print('socket ok')",
            allow_network=True,
        )
        assert result["success"] is True
        assert "socket ok" in result["stdout"]

    def test_subprocess_execution_still_blocked_when_network_enabled(self) -> None:
        result = execute_python_code.run(
            code="import subprocess; subprocess.run(['echo', 'nope'])",
            allow_network=True,
        )
        assert result["success"] is False
        assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]

    def test_filesystem_write_outside_tmp_blocked_when_network_enabled(self) -> None:
        result = execute_python_code.run(
            code="open('/etc/python_execution_tool_test', 'w').write('x')",
            allow_network=True,
        )
        assert result["success"] is False
        assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]


class TestPythonExecutionToolCredentials:
    def test_github_token_from_env_is_available_and_redacted(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_token")
        result = execute_python_code.run(
            code=("import os\nprint('token=' + str(os.environ.get('GITHUB_TOKEN')))\n")
        )
        assert result["success"] is True
        assert result["credentials_available"] == ["github"]
        assert "ghp_secret_token" not in result["stdout"]
        assert "[redacted]" in result["stdout"]

    def test_explicit_github_token_is_available_and_redacted(self, monkeypatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        result = execute_python_code.run(
            code="import os; print(os.environ.get('GITHUB_TOKEN'))",
            github_token="ghp_explicit_secret",
        )
        assert result["success"] is True
        assert result["credentials_available"] == ["github"]
        assert "ghp_explicit_secret" not in result["stdout"]
        assert "[redacted]" in result["stdout"]


class TestMapExecutePythonCode:
    def test_records_stdout_on_success(self) -> None:
        evidence: dict[str, Any] = {}
        map_execute_python_code(evidence, {"success": True, "stdout": "42\n"}, {})
        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "execute_python_code"
        assert entries[0]["summary"] == "42"

    def test_skips_successful_run_with_no_output(self) -> None:
        evidence: dict[str, Any] = {}
        map_execute_python_code(evidence, {"success": True, "stdout": ""}, {})
        assert "catalog_entries" not in evidence

    def test_records_failure_reason_even_without_output(self) -> None:
        """A failure is diagnostic information by itself -- unlike an empty
        successful run, it must not be silently dropped."""
        evidence: dict[str, Any] = {}
        map_execute_python_code(
            evidence, {"success": False, "timed_out": True, "stdout": "", "stderr": ""}, {}
        )
        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["summary"] == "execution failed (timed out)"

    def test_records_failure_with_stderr_detail(self) -> None:
        evidence: dict[str, Any] = {}
        map_execute_python_code(
            evidence,
            {"success": False, "exit_code": 1, "stderr": "ZeroDivisionError: division by zero"},
            {},
        )
        summary = evidence["catalog_entries"][0]["summary"]
        assert summary.startswith("execution failed (exit code 1)")
        assert "ZeroDivisionError" in summary

    def test_disambiguates_repeat_calls(self) -> None:
        """Regression: this tool can be called many times per investigation
        with different code -- record_evidence_entry lets the first entry
        for a source win, so reusing one source key would silently drop
        every call after the first."""
        evidence: dict[str, Any] = {}
        map_execute_python_code(evidence, {"success": True, "stdout": "1"}, {})
        map_execute_python_code(evidence, {"success": True, "stdout": "2"}, {})
        sources = [e["source"] for e in evidence["catalog_entries"]]
        assert sources == ["execute_python_code", "execute_python_code#2"]
