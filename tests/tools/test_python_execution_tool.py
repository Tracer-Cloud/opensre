"""Tests for the Python execution tool."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from core.agent_harness.turns.display_text import format_generic_tool_payload
from core.llm.types import ToolCall
from infrastructure.safety.sandbox.runner import SandboxResult
from tests.tools.conftest import BaseToolContract
from tools.registry import clear_tool_registry_cache, get_registered_tool_map
from tools.system.python_execution_tool import execute_python_code
from tools.system.python_execution_tool._evidence import map_execute_python_code


def _run(**kwargs: Any) -> dict[str, Any]:
    """The payload of one run; the model-facing text is checked separately."""
    payload = execute_python_code.run(**kwargs).details
    assert isinstance(payload, dict)
    return payload


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
        assert '<tool_guidance name="measuring-github-star-velocity"' in registered.skill_guidance
        assert marker in registered.skill_guidance
        assert marker in registered.description


class TestPythonExecutionToolExecution:
    def test_successful_execution_returns_stdout(self) -> None:
        result = _run(code="print('hello world')")
        assert result["success"] is True
        assert "hello world" in result["stdout"]
        assert result["stderr"] == ""
        assert result["exit_code"] == 0

    def test_inputs_are_injected(self) -> None:
        result = _run(
            code="print(inputs['owner'] + '/' + inputs['repo'])",
            inputs={"owner": "Tracer-Cloud", "repo": "opensre"},
        )
        assert result["success"] is True
        assert "Tracer-Cloud/opensre" in result["stdout"]
        assert result["inputs"]["owner"] == "Tracer-Cloud"
        assert result["inputs"]["repo"] == "opensre"
        assert "opensre_runtime" in result["inputs"]

    def test_failure_returns_non_zero_exit_code(self) -> None:
        result = _run(code="raise RuntimeError('boom')")
        assert result["success"] is False
        assert result["exit_code"] != 0
        assert "RuntimeError" in result["stderr"]

    def test_timeout_produces_timed_out_true(self) -> None:
        result = _run(code="import time; time.sleep(10)", timeout=1)
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
        result = _run(code="import socket; socket.socket()")
        assert result["success"] is False
        assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]

    def test_network_access_can_be_enabled(self) -> None:
        result = _run(
            code="import socket; s = socket.socket(); s.close(); print('socket ok')",
            allow_network=True,
        )
        assert result["success"] is True
        assert "socket ok" in result["stdout"]

    def test_subprocess_execution_still_blocked_when_network_enabled(self) -> None:
        result = _run(
            code="import subprocess; subprocess.run(['echo', 'nope'])",
            allow_network=True,
        )
        assert result["success"] is False
        assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]

    def test_filesystem_write_outside_tmp_blocked_when_network_enabled(self) -> None:
        result = _run(
            code="open('/etc/python_execution_tool_test', 'w').write('x')",
            allow_network=True,
        )
        assert result["success"] is False
        assert "PermissionError" in result["stderr"] or "PermissionError" in result["stdout"]


class TestPythonExecutionToolCredentials:
    def test_github_token_from_env_is_not_available(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret_token")
        result = _run(code=("import os\nprint('token=' + str(os.environ.get('GITHUB_TOKEN')))\n"))
        assert result["success"] is True
        assert result["credentials_available"] == []
        assert "ghp_secret_token" not in result["stdout"]
        assert "ghp_env_secret" not in result["stdout"]

    def test_explicit_github_token_is_available_and_redacted(self, monkeypatch) -> None:
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        result = _run(
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


class TestWhatTheModelReadsBack:
    """The observation is the status and the two streams; the payload keeps the rest."""

    def test_the_call_is_not_echoed_back(self) -> None:
        """The code and inputs the model had just written were two thirds of a typical result."""
        # Arrange
        code = "MARKER_ONLY_IN_THE_CODE = 1\nprint('total', inputs['count'] * 2)\n"

        # Act
        result = execute_python_code.run(code=code, inputs={"count": 21})

        # Assert
        assert result.content.splitlines()[:3] == [
            "Process exited with code 0",
            "Stdout:",
            "total 42",
        ]
        assert "MARKER_ONLY_IN_THE_CODE" not in result.content
        assert "opensre_runtime" not in result.content
        assert result.provider_content() == result.content
        # Callers that inspect the run still get all of it.
        assert result.details["code"] == code
        assert result.details["inputs"]["count"] == 21
        assert result.details["success"] is True

    def test_a_failure_shows_its_traceback_with_the_lines_of_the_code(self) -> None:
        result = execute_python_code.run(code="values = [1, 2]\nprint('before')\nvalues[5]\n")

        assert result.content.startswith("Process exited with code 1\nStdout:\nbefore\n")
        assert 'File "generated_code.py", line 3' in result.content
        assert "IndexError" in result.content
        assert result.details["success"] is False
        # Code that ran and raised is a result to read, not a failed call.
        assert result.is_error is False

    def test_output_past_the_budget_cannot_push_the_status_out(self, monkeypatch) -> None:
        """Cut as one JSON string, a long stdout decided whether the exit code survived."""
        # Arrange: a budget far smaller than what the code prints.
        monkeypatch.setenv("OPENSRE_TOOL_OUTPUT_TOKEN_LIMIT", "200")

        # Act
        result = execute_python_code.run(code="print('x' * 20000)\nraise SystemExit(3)\n")

        # Assert
        assert result.content.startswith("Process exited with code 3\nStdout:\n")
        assert "tokens truncated" in result.content
        assert len(result.content) < 1200
        assert len(result.details["stdout"]) > 20000

    def test_a_timeout_reports_the_output_written_before_it(self) -> None:
        result = execute_python_code.run(
            code="import time\nprint('page 1 done')\ntime.sleep(30)\n", timeout=1
        )

        assert result.content.splitlines()[0] == (
            "Execution timed out after 1 seconds; output written before then is below."
        )
        assert "page 1 done" in result.content
        assert result.details["timed_out"] is True
        # A run the sandbox stopped is a failed call; error counts read this flag.
        assert result.is_error is True

    def test_a_blocked_native_import_names_the_way_out(self) -> None:
        """numpy and pandas load ctypes, which the sandbox refuses; the traceback does not say so."""
        result = execute_python_code.run(code="import ctypes\n")

        assert result.details["success"] is False
        assert "Hint: a package tried to load native code through ctypes" in result.content
        assert "standard library" in result.content

    def test_a_missing_module_names_the_way_out(self) -> None:
        result = execute_python_code.run(code="import module_that_is_not_installed_anywhere\n")

        assert (
            "Hint: module_that_is_not_installed_anywhere is not installed in the sandbox"
            in result.content
        )

    def test_the_transcript_shows_one_line_not_what_the_model_reads(self) -> None:
        """Plain-text content with no summary is printed whole under the tool's name."""
        # Arrange
        call = ToolCall(id="call-1", name="execute_python_code", input={})

        # Act
        printed = execute_python_code.run(code="print('a')\nprint('b')\n")
        failed = execute_python_code.run(code="print('a')\n1 / 0\n")
        timed_out = execute_python_code.run(code="import time\ntime.sleep(30)\n", timeout=1)

        # Assert
        assert format_generic_tool_payload(call, printed) == "Ran and printed 2 lines"
        assert format_generic_tool_payload(call, failed) == (
            "Exited with code 1: ZeroDivisionError: division by zero"
        )
        assert format_generic_tool_payload(call, timed_out) == "Execution timed out after 1 seconds"
