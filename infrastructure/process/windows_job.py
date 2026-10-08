"""Own a native Windows command's descendants from launch until cleanup."""

from __future__ import annotations

import contextlib
import ctypes
import logging
import math
import os
import subprocess
import time
from collections.abc import Iterator, Mapping
from contextlib import ExitStack, contextmanager
from typing import IO

from infrastructure.process._windows_api import (
    DWORD,
    HANDLE,
    SIZE_T,
    BasicAccountingInformation,
    ExtendedLimitInformation,
    ProcessInformation,
    StartupInfoEx,
    WindowsAPI,
)

_CREATE_SUSPENDED = 0x00000004
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_STARTF_USESTDHANDLES = 0x00000100
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_DUPLICATE_SAME_ACCESS = 2
_STD_INPUT_HANDLE = -10 & 0xFFFFFFFF
_WAIT_TIMEOUT = 258
_WAIT_OBJECT_0 = 0
_INFINITE = 0xFFFFFFFF
_CLEANUP_WAIT_SECONDS = 5.0
_LOGGER = logging.getLogger(__name__)


class _Handle:
    def __init__(self, api: WindowsAPI, value: int) -> None:
        self.api = api
        self.value = value

    def close(self) -> None:
        if self.value:
            self.api.check(self.api.dll.CloseHandle(self.value))
            self.value = 0


class _Descriptor:
    def __init__(self, value: int) -> None:
        self.value = value

    def close(self) -> None:
        if self.value >= 0:
            os.close(self.value)
            self.value = -1


def _own_handle(stack: ExitStack, api: WindowsAPI, value: int) -> _Handle:
    handle = _Handle(api, value)
    stack.callback(handle.close)
    return handle


def _pipe(stack: ExitStack) -> tuple[IO[str], _Descriptor, int]:
    import msvcrt

    reader_fd, writer_fd = os.pipe()
    reader, writer = _Descriptor(reader_fd), _Descriptor(writer_fd)
    stack.callback(reader.close)
    stack.callback(writer.close)
    stream = os.fdopen(reader.value, "r", encoding="utf-8", errors="replace")
    reader.value = -1
    stack.callback(stream.close)
    handle = msvcrt.__dict__["get_osfhandle"](writer.value)
    os.__dict__["set_handle_inheritable"](handle, True)
    return stream, writer, handle


def _stdin_handle(stack: ExitStack, api: WindowsAPI) -> _Handle:
    import msvcrt

    source = api.dll.GetStdHandle(_STD_INPUT_HANDLE)
    descriptor: int | None = None
    try:
        if source in (None, 0, ctypes.c_void_p(-1).value):
            descriptor = os.open(os.devnull, os.O_RDONLY)
            source = msvcrt.__dict__["get_osfhandle"](descriptor)
        duplicate = HANDLE()
        current = api.dll.GetCurrentProcess()
        api.check(
            api.dll.DuplicateHandle(
                current, source, current, ctypes.byref(duplicate), 0, True, _DUPLICATE_SAME_ACCESS
            )
        )
    finally:
        if descriptor is not None:
            os.close(descriptor)
    assert duplicate.value is not None
    return _own_handle(stack, api, duplicate.value)


def _environment_block(environment: Mapping[str, str]) -> str:
    for name, value in environment.items():
        if not name or "=" in name[1:] or "\0" in name or "\0" in value:
            raise ValueError("invalid environment variable")
    entries = sorted(environment.items(), key=lambda item: item[0].upper())
    return "\0".join(f"{name}={value}" for name, value in entries) + "\0\0"


class WindowsJobProcess:
    """A process handle plus the kernel-owned descendant set for one shell call."""

    def __init__(
        self,
        api: WindowsAPI,
        job: _Handle,
        process: _Handle,
        pid: int,
        command: str,
        stdout: IO[str],
        stderr: IO[str],
    ) -> None:
        self._api = api
        self._job = job
        self._process = process
        self.pid = pid
        self.args = command
        self.stdout = stdout
        self.stderr = stderr
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        status = self._api.dll.WaitForSingleObject(self._process.value, 0)
        if status == _WAIT_TIMEOUT:
            return None
        if status != _WAIT_OBJECT_0:
            raise self._api.error()
        code = DWORD()
        self._api.check(self._api.dll.GetExitCodeProcess(self._process.value, ctypes.byref(code)))
        self.returncode = code.value
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        milliseconds = (
            _INFINITE if timeout is None else min(_INFINITE - 1, max(0, math.ceil(timeout * 1000)))
        )
        status = self._api.dll.WaitForSingleObject(self._process.value, milliseconds)
        if status == _WAIT_TIMEOUT:
            assert timeout is not None
            raise subprocess.TimeoutExpired(self.args, timeout)
        if status != _WAIT_OBJECT_0:
            raise self._api.error()
        result = self.poll()
        assert result is not None
        return result

    def is_alive(self) -> bool:
        if not self._job.value:
            return False
        information = BasicAccountingInformation()
        self._api.check(
            self._api.dll.QueryInformationJobObject(
                self._job.value,
                _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                ctypes.byref(information),
                ctypes.sizeof(information),
                None,
            )
        )
        return bool(information.ActiveProcesses)

    def terminate_tree(self) -> None:
        if not self._job.value:
            return
        try:
            self._api.check(self._api.dll.TerminateJobObject(self._job.value, 1))
            deadline = time.monotonic() + _CLEANUP_WAIT_SECONDS
            while self.is_alive():
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(self.args, _CLEANUP_WAIT_SECONDS)
                time.sleep(0.01)
            self.wait(timeout=_CLEANUP_WAIT_SECONDS)
        except (OSError, subprocess.TimeoutExpired) as exc:
            _LOGGER.warning(
                "Windows tree cleanup failed (%s); closing owned job", type(exc).__name__
            )
            # This checked close invokes KILL_ON_JOB_CLOSE before reader joins.
            # If it fails, retain the handle and propagate: cleanup is unproven.
            self._job.close()
            try:
                if self.poll() is None:
                    self.terminate()
            except OSError as root_error:
                _LOGGER.warning(
                    "Windows root termination after job closure failed (%s)",
                    type(root_error).__name__,
                )
            try:
                self.wait(timeout=_CLEANUP_WAIT_SECONDS)
            except (OSError, subprocess.TimeoutExpired) as wait_error:
                _LOGGER.warning(
                    "Windows root exit after job closure was not confirmed (%s)",
                    type(wait_error).__name__,
                )

    def terminate(self) -> None:
        self._api.check(self._api.dll.TerminateProcess(self._process.value, 1))

    def kill(self) -> None:
        self.terminate()


@contextmanager
def spawn_windows_job(
    command: str, *, environment: Mapping[str, str]
) -> Iterator[WindowsJobProcess]:
    """Start a suspended command, assign ownership, then allow it to execute."""
    if "\0" in command:
        raise ValueError("embedded null character")
    environment_buffer = ctypes.create_unicode_buffer(_environment_block(environment))
    command_buffer = ctypes.create_unicode_buffer(command)
    api = WindowsAPI()
    with ExitStack() as stack:
        job_value = api.dll.CreateJobObjectW(None, None)
        api.check(job_value)
        job = _own_handle(stack, api, job_value)
        limits = ExtendedLimitInformation()
        limits.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        api.check(
            api.dll.SetInformationJobObject(
                job.value,
                _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits),
                ctypes.sizeof(limits),
            )
        )

        stdout, stdout_writer, stdout_handle = _pipe(stack)
        stderr, stderr_writer, stderr_handle = _pipe(stack)
        stdin = _stdin_handle(stack, api)
        inherited_handles = (HANDLE * 3)(stdin.value, stdout_handle, stderr_handle)
        attribute_size = SIZE_T()
        api.dll.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(attribute_size))
        if not attribute_size.value:
            raise api.error()
        attributes = ctypes.create_string_buffer(attribute_size.value)
        api.check(
            api.dll.InitializeProcThreadAttributeList(
                attributes, 1, 0, ctypes.byref(attribute_size)
            )
        )
        process = _own_handle(stack, api, 0)
        thread = _own_handle(stack, api, 0)
        owned = WindowsJobProcess(api, job, process, 0, command, stdout, stderr)
        information = ProcessInformation()
        assigned = False
        try:
            with ExitStack() as startup_cleanup:
                startup_cleanup.callback(api.dll.DeleteProcThreadAttributeList, attributes)
                api.check(
                    api.dll.UpdateProcThreadAttribute(
                        attributes,
                        0,
                        _PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                        inherited_handles,
                        ctypes.sizeof(inherited_handles),
                        None,
                        None,
                    )
                )
                startup = StartupInfoEx()
                startup.StartupInfo.cb = ctypes.sizeof(startup)
                startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
                startup.StartupInfo.hStdInput = stdin.value
                startup.StartupInfo.hStdOutput = stdout_handle
                startup.StartupInfo.hStdError = stderr_handle
                startup.lpAttributeList = ctypes.addressof(attributes)
                api.check(
                    api.dll.CreateProcessW(
                        None,
                        command_buffer,
                        None,
                        None,
                        True,
                        _CREATE_SUSPENDED
                        | _CREATE_UNICODE_ENVIRONMENT
                        | _EXTENDED_STARTUPINFO_PRESENT,
                        environment_buffer,
                        None,
                        ctypes.byref(startup),
                        ctypes.byref(information),
                    )
                )
                process.value = information.hProcess
                thread.value = information.hThread
                owned.pid = information.dwProcessId
            api.check(api.dll.AssignProcessToJobObject(job.value, process.value))
            assigned = True
            if api.dll.ResumeThread(thread.value) == _INFINITE:
                raise api.error()
            thread.close()
            stdout_writer.close()
            stderr_writer.close()
            stdin.close()
            yield owned
        finally:
            # Also covers failure before assignment/resume. Never leave a
            # suspended root behind, and never resolve ownership via its PID.
            if assigned:
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    owned.terminate_tree()
            # Kill-on-close must run before stream.close can wait on a reader
            # whose pipe is still held by a descendant after a cleanup error.
            job.close()
            if process.value:
                with contextlib.suppress(OSError):
                    owned.terminate()
                with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                    owned.wait(timeout=_CLEANUP_WAIT_SECONDS)


__all__ = ["WindowsJobProcess", "spawn_windows_job"]
