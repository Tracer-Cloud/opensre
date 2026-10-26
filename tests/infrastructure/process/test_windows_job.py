"""Windows job ownership and launch-failure regressions."""

from __future__ import annotations

import ctypes
import io
import os
import subprocess
import sys
import threading
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from infrastructure.process import windows_job
from infrastructure.process._windows_api import ProcessInformation, WindowsAPI
from tools.interactive_shell.shell.execution import _collect_shell_result

_NATIVE_WINDOWS = pytest.mark.skipif(os.name != "nt", reason="requires native Windows jobs")


def test_environment_block_preserves_values_and_rejects_injected_entries() -> None:
    assert windows_job._environment_block({"z": "café", "A": "x=y", "=C:": "C:\\work"}) == (
        "=C:=C:\\work\0A=x=y\0z=café\0\0"
    )
    with pytest.raises(ValueError, match="environment"):
        windows_job._environment_block({"SAFE": "value\0INJECTED=yes"})
    with pytest.raises(ValueError, match="environment"):
        windows_job._environment_block({"BAD=NAME": "value"})


@_NATIVE_WINDOWS
def test_stdin_fallback_descriptor_closes_when_duplication_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import msvcrt

    descriptor = 123
    closed: list[int] = []
    api = WindowsAPI()

    def _missing_stdin(_kind: int) -> int:
        return 0

    def _open_null(_path: str, _flags: int) -> int:
        return descriptor

    def _descriptor_handle(value: int) -> int:
        assert value == descriptor
        return 456

    def _close(value: int) -> None:
        closed.append(value)

    def _fail_duplicate(*_args: Any) -> int:
        raise OSError("simulated duplication failure")

    monkeypatch.setattr(api.dll, "GetStdHandle", _missing_stdin)
    monkeypatch.setattr(api.dll, "DuplicateHandle", _fail_duplicate)
    monkeypatch.setattr(windows_job.os, "open", _open_null)
    monkeypatch.setattr(windows_job.os, "close", _close)
    monkeypatch.setattr(msvcrt, "get_osfhandle", _descriptor_handle)

    duplicate_error: OSError | None = None
    try:
        with ExitStack() as stack:
            windows_job._stdin_handle(stack, api)
    except OSError as error:
        duplicate_error = error

    assert duplicate_error is not None, "expected handle duplication to fail"
    assert str(duplicate_error) == "simulated duplication failure"
    assert closed == [descriptor]


def test_command_nul_is_rejected_before_any_windows_launch() -> None:
    with (
        pytest.raises(ValueError, match="null"),
        windows_job.spawn_windows_job("echo safe\0&echo different", environment={}),
    ):
        pytest.fail("a command containing NUL must never execute")


@pytest.mark.parametrize("failure", ["terminate", "query", "deadline"])
def test_cleanup_fallback_preserves_captured_outcome(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure: str,
) -> None:
    alive = True
    closed: list[int] = []

    def _close(handle: int) -> int:
        nonlocal alive
        closed.append(handle)
        assert handle == 41
        alive = False
        return 1

    def _wait(_handle: int, _milliseconds: int) -> int:
        return 258 if alive else 0

    def _exit_code(_handle: int, value: Any) -> int:
        ctypes.cast(value, ctypes.POINTER(windows_job.DWORD)).contents.value = 1
        return 1

    def _terminate(_job: int, _code: int) -> int:
        if failure == "terminate":
            raise OSError("injected termination failure")
        return 1

    def _query(job: int, _kind: int, value: Any, _size: int, _length: Any) -> int:
        assert job == 41
        if failure == "query":
            raise OSError("injected query failure")
        information = ctypes.cast(
            value, ctypes.POINTER(windows_job.BasicAccountingInformation)
        ).contents
        information.ActiveProcesses = 1
        return 1

    api = WindowsAPI.__new__(WindowsAPI)
    api.dll = SimpleNamespace(
        CloseHandle=_close,
        WaitForSingleObject=_wait,
        GetExitCodeProcess=_exit_code,
        TerminateJobObject=_terminate,
        QueryInformationJobObject=_query,
    )
    process = windows_job.WindowsJobProcess(
        api,
        windows_job._Handle(api, 41),
        windows_job._Handle(api, 42),
        43,
        "secret-token-do-not-log",
        io.StringIO("captured stdout\n"),
        io.StringIO("captured stderr\n"),
    )
    cancel = threading.Event()
    if failure == "terminate":
        cancel.set()
    monkeypatch.setattr(windows_job, "_CLEANUP_WAIT_SECONDS", 0)

    result = _collect_shell_result(
        process,
        command=process.args,
        watch_cancel=cancel,
        timeout_seconds=10 if cancel.is_set() else 0,
        max_output_bytes=1000,
        owned_tree=process,
    )

    assert result.cancelled is cancel.is_set()
    assert result.timed_out is not cancel.is_set()
    assert result.stdout == "captured stdout\n"
    assert result.stderr == "captured stderr\n"
    assert result.exit_code == 1
    assert closed == [41]
    assert not alive
    assert not process.is_alive()
    process.terminate_tree()
    assert closed == [41]
    assert "closing owned job" in caplog.text
    assert process.args not in caplog.text


def test_failed_job_close_retains_ownership_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    api = WindowsAPI.__new__(WindowsAPI)

    def _refuse(*_args: Any) -> int:
        return 0

    def _error() -> OSError:
        return OSError("injected handle failure")

    api.dll = SimpleNamespace(TerminateJobObject=_refuse, CloseHandle=_refuse)
    monkeypatch.setattr(api, "error", _error)
    job = windows_job._Handle(api, 41)
    process = windows_job.WindowsJobProcess(
        api, job, windows_job._Handle(api, 42), 43, "command", io.StringIO(), io.StringIO()
    )

    with pytest.raises(OSError, match="handle failure"):
        process.terminate_tree()

    assert job.value == 41


@_NATIVE_WINDOWS
@pytest.mark.parametrize("exit_code", [7, 259])
def test_root_exit_code_is_preserved_including_still_active_value(exit_code: int) -> None:
    command = subprocess.list2cmdline([sys.executable, "-c", f"raise SystemExit({exit_code})"])
    with windows_job.spawn_windows_job(command, environment=os.environ) as process:
        assert process.wait(timeout=10) == exit_code
        assert process.poll() == exit_code


@_NATIVE_WINDOWS
def test_command_can_create_a_nested_owned_job(tmp_path: Path) -> None:
    script = tmp_path / "nested_job.py"
    script.write_text(
        "import os, subprocess, sys\n"
        "from infrastructure.process.windows_job import spawn_windows_job\n"
        "command = subprocess.list2cmdline([sys.executable, '-c', 'print(\"nested\")'])\n"
        "with spawn_windows_job(command, environment=os.environ) as child:\n"
        "    assert child.wait(timeout=10) == 0\n"
        "    assert child.stdout.read().strip() == 'nested'\n"
        "print('nested job completed', flush=True)\n",
        encoding="utf-8",
    )
    command = subprocess.list2cmdline([sys.executable, str(script)])
    with windows_job.spawn_windows_job(command, environment=os.environ) as process:
        assert process.wait(timeout=15) == 0, process.stderr.read()
        assert process.stdout.read().strip() == "nested job completed"


@_NATIVE_WINDOWS
def test_raw_command_environment_streams_and_inherited_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import msvcrt

    script = tmp_path / "script with spaces.py"
    script.write_text(
        "import os, sys\n"
        "os.write(1, (os.environ['OPENSRE_JOB_TEST'] + ':' + sys.stdin.read()).encode('utf-8'))\n"
        "os.write(2, b'warning')\n",
        encoding="utf-8",
    )
    reader, writer = os.pipe()
    original_api = windows_job.WindowsAPI

    def _api_with_stdin() -> WindowsAPI:
        api = original_api()

        def _stdin(_kind: int) -> int:
            return msvcrt.get_osfhandle(reader)

        api.dll.GetStdHandle = _stdin
        return api

    monkeypatch.setattr(windows_job, "WindowsAPI", _api_with_stdin)
    os.write(writer, b"inherited input")
    os.close(writer)
    environment = {**os.environ, "OPENSRE_JOB_TEST": "café"}
    command = subprocess.list2cmdline([sys.executable, str(script)])
    try:
        with windows_job.spawn_windows_job(command, environment=environment) as process:
            assert process.wait(timeout=10) == 0
            assert process.stdout.read() == "café:inherited input"
            assert process.stderr.read() == "warning"
    finally:
        os.close(reader)


@_NATIVE_WINDOWS
@pytest.mark.parametrize("failure_stage", ["assign", "resume"])
def test_launch_failure_reaps_root_before_it_can_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    marker = tmp_path / "must-not-run"
    command = subprocess.list2cmdline(
        [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"]
    )
    roots: list[psutil.Process] = []
    original_api = windows_job.WindowsAPI

    def _failing_api() -> WindowsAPI:
        api = original_api()
        original_create = api.dll.CreateProcessW

        def _record_root(*args: Any) -> int:
            result = int(original_create(*args))
            if result:
                information = ctypes.cast(args[-1], ctypes.POINTER(ProcessInformation)).contents
                roots.append(psutil.Process(information.dwProcessId))
            return result

        def _refuse_assignment(_job: int, _root: int) -> int:
            ctypes.set_last_error(5)
            return 0

        def _refuse_resume(_thread: int) -> int:
            ctypes.set_last_error(5)
            return 0xFFFFFFFF

        api.dll.CreateProcessW = _record_root
        if failure_stage == "assign":
            api.dll.AssignProcessToJobObject = _refuse_assignment
        else:
            api.dll.ResumeThread = _refuse_resume
        return api

    monkeypatch.setattr(windows_job, "WindowsAPI", _failing_api)
    try:
        with pytest.raises(OSError), windows_job.spawn_windows_job(command, environment=os.environ):
            pytest.fail("failed launch must not yield an executing command")
        assert len(roots) == 1
        assert not roots[0].is_running()
        assert not marker.exists()
    finally:
        for root in roots:
            if root.is_running():
                root.kill()
                root.wait(timeout=5)


@_NATIVE_WINDOWS
@pytest.mark.timeout(20)
def test_exception_closes_job_before_closing_captured_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = (
        "import subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "print(child.pid, flush=True); time.sleep(30)"
    )
    command = subprocess.list2cmdline([sys.executable, "-c", script])
    processes: list[psutil.Process] = []
    reader: threading.Thread | None = None
    reader_started = threading.Event()

    def _failed_termination(_self: windows_job.WindowsJobProcess) -> None:
        raise OSError("simulated job termination failure")

    try:
        consumer_error: RuntimeError | None = None
        try:
            with windows_job.spawn_windows_job(command, environment=os.environ) as process:
                processes.append(psutil.Process(process.pid))
                processes.append(psutil.Process(int(process.stdout.readline())))

                def _read_to_eof() -> None:
                    reader_started.set()
                    process.stdout.read()

                reader = threading.Thread(target=_read_to_eof, daemon=True)
                reader.start()
                assert reader_started.wait(timeout=2)
                monkeypatch.setattr(
                    windows_job.WindowsJobProcess, "terminate_tree", _failed_termination
                )
                raise RuntimeError("consumer failure")
        except RuntimeError as error:
            consumer_error = error
        assert consumer_error is not None, "expected consumer failure to propagate"
        assert str(consumer_error) == "consumer failure"
        for descendant in processes:
            descendant.wait(timeout=5)
            assert not descendant.is_running()
        reader.join(timeout=2)
        assert not reader.is_alive()
    finally:
        for descendant in reversed(processes):
            if descendant.is_running():
                descendant.kill()
                descendant.wait(timeout=5)
        if reader is not None:
            reader.join(timeout=2)
