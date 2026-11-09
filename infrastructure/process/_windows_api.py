"""Documented Win32 process and job ABI, loaded only on Windows."""

from __future__ import annotations

import ctypes
from typing import Any

DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
HANDLE = ctypes.c_void_p
SIZE_T = ctypes.c_size_t
BOOL = ctypes.c_int32


class StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", DWORD),
        ("dwY", DWORD),
        ("dwXSize", DWORD),
        ("dwYSize", DWORD),
        ("dwXCountChars", DWORD),
        ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags", DWORD),
        ("wShowWindow", WORD),
        ("cbReserved2", WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", HANDLE),
        ("hStdOutput", HANDLE),
        ("hStdError", HANDLE),
    ]


class StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", HANDLE),
        ("hThread", HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
    ]


class BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", DWORD),
        ("MinimumWorkingSetSize", SIZE_T),
        ("MaximumWorkingSetSize", SIZE_T),
        ("ActiveProcessLimit", DWORD),
        ("Affinity", SIZE_T),
        ("PriorityClass", DWORD),
        ("SchedulingClass", DWORD),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_uint64),
        ("WriteOperationCount", ctypes.c_uint64),
        ("OtherOperationCount", ctypes.c_uint64),
        ("ReadTransferCount", ctypes.c_uint64),
        ("WriteTransferCount", ctypes.c_uint64),
        ("OtherTransferCount", ctypes.c_uint64),
    ]


class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BasicLimitInformation),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", SIZE_T),
        ("JobMemoryLimit", SIZE_T),
        ("PeakProcessMemoryUsed", SIZE_T),
        ("PeakJobMemoryUsed", SIZE_T),
    ]


class BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", DWORD),
        ("TotalProcesses", DWORD),
        ("ActiveProcesses", DWORD),
        ("TotalTerminatedProcesses", DWORD),
    ]


class WindowsAPI:
    """Bind pointer-sized handles and explicit structure layouts to kernel32."""

    def __init__(self) -> None:
        self.dll: Any = ctypes.__dict__["WinDLL"]("kernel32", use_last_error=True)
        pointer = ctypes.c_void_p
        signatures: dict[str, tuple[Any, list[Any]]] = {
            "CloseHandle": (BOOL, [HANDLE]),
            "CreateJobObjectW": (HANDLE, [pointer, ctypes.c_wchar_p]),
            "SetInformationJobObject": (BOOL, [HANDLE, ctypes.c_int, pointer, DWORD]),
            "QueryInformationJobObject": (BOOL, [HANDLE, ctypes.c_int, pointer, DWORD, pointer]),
            "AssignProcessToJobObject": (BOOL, [HANDLE, HANDLE]),
            "TerminateJobObject": (BOOL, [HANDLE, DWORD]),
            "TerminateProcess": (BOOL, [HANDLE, DWORD]),
            "GetCurrentProcess": (HANDLE, []),
            "GetStdHandle": (HANDLE, [DWORD]),
            "DuplicateHandle": (BOOL, [HANDLE, HANDLE, HANDLE, pointer, DWORD, BOOL, DWORD]),
            "WaitForSingleObject": (DWORD, [HANDLE, DWORD]),
            "GetExitCodeProcess": (BOOL, [HANDLE, pointer]),
            "ResumeThread": (DWORD, [HANDLE]),
            "InitializeProcThreadAttributeList": (BOOL, [pointer, DWORD, DWORD, pointer]),
            "UpdateProcThreadAttribute": (
                BOOL,
                [pointer, DWORD, SIZE_T, pointer, SIZE_T, pointer, pointer],
            ),
            "DeleteProcThreadAttributeList": (None, [pointer]),
            "CreateProcessW": (
                BOOL,
                [
                    ctypes.c_wchar_p,
                    pointer,
                    pointer,
                    pointer,
                    BOOL,
                    DWORD,
                    pointer,
                    ctypes.c_wchar_p,
                    pointer,
                    pointer,
                ],
            ),
        }
        for name, (result_type, argument_types) in signatures.items():
            function = getattr(self.dll, name)
            function.restype = result_type
            function.argtypes = argument_types

    def error(self) -> OSError:
        error: OSError = ctypes.__dict__["WinError"](ctypes.__dict__["get_last_error"]())
        return error

    def check(self, succeeded: int) -> None:
        if not succeeded:
            raise self.error()


__all__ = [
    "BasicAccountingInformation",
    "DWORD",
    "ExtendedLimitInformation",
    "HANDLE",
    "ProcessInformation",
    "SIZE_T",
    "StartupInfoEx",
    "WindowsAPI",
]
