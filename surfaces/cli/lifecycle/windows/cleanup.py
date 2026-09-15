"""Schedule serialized, identity-bound PowerShell cleanup after the CLI exits."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import surfaces.cli.lifecycle.windows.powershell as powershell
from config.constants.installer import (
    WINDOWS_BINARY_FILENAME,
    WINDOWS_INSTALL_LOCK_FILENAME,
    WINDOWS_LAUNCHER_FILENAME,
    WINDOWS_LAYOUT_MARKER_TEXT,
)
from surfaces.cli.lifecycle.windows.paths import (
    UnsafeWindowsPathError,
    canonical_existing_path,
    ensure_no_reparse_ancestors,
    ensure_tree_has_no_reparse_points,
    windows_path_exists,
    windows_path_is_within,
)
from surfaces.cli.lifecycle.windows.processes import (
    WindowsProcessIdentity,
    windows_process_identity,
)

CLEANUP_SCRIPT_PATH = Path(__file__).with_name("uninstall_cleanup.ps1")
_WINDOWS_EPOCH_FILETIME = 116_444_736_000_000_000
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WINDOWS_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WINDOWS_FILE_SHARE_ALL = 0x00000001 | 0x00000002 | 0x00000004
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_GENERIC_WRITE = 0x40000000
_WINDOWS_DELETE = 0x00010000
_WINDOWS_ERROR_ALREADY_EXISTS = 183
_WINDOWS_FILE_DISPOSITION_INFO = 4
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_OPEN_ALWAYS = 4


class _WindowsByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


class _WindowsFileDispositionInformation(ctypes.Structure):
    _fields_ = [("delete_file", wintypes.BOOLEAN)]


@dataclass
class _CleanupLockLease:
    payload: dict[str, object]
    path: Path
    created: bool
    native_handle: int | None = None
    descriptor: int | None = None
    committed: bool = False

    def close(self) -> None:
        """Close the lock, deleting only a newly created uncommitted file."""
        if self.native_handle is not None:
            try:
                if self.created and not self.committed:
                    _delete_windows_file_by_handle(self.native_handle)
            finally:
                _close_windows_handle(self.native_handle)
                self.native_handle = None
            return
        if self.descriptor is None:
            return
        try:
            if self.created and not self.committed:
                opened = os.fstat(self.descriptor)
                current = self.path.stat(follow_symlinks=False)
                if (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino):
                    self.path.unlink()
        except FileNotFoundError:
            pass
        finally:
            os.close(self.descriptor)
            self.descriptor = None


def read_cleanup_script() -> str:
    """Return the packaged cleanup worker source copied to a private temporary file."""
    return CLEANUP_SCRIPT_PATH.read_text(encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _creation_filetime(path: Path) -> int:
    metadata = path.stat(follow_symlinks=False)
    return metadata.st_ctime_ns // 100 + _WINDOWS_EPOCH_FILETIME


def _fallback_file_identity(path: Path) -> tuple[int, int, int]:
    metadata = path.stat(follow_symlinks=False)
    return (
        int(metadata.st_dev) & 0xFFFFFFFF,
        int(metadata.st_ino) & 0xFFFFFFFFFFFFFFFF,
        metadata.st_ctime_ns // 100 + _WINDOWS_EPOCH_FILETIME,
    )


def _ordinary_windows_path(path: str | Path) -> Path:
    path = str(path)
    if path.startswith("\\\\?\\UNC\\"):
        return Path("\\\\" + path[8:])
    if path.startswith("\\\\?\\"):
        return Path(path[4:])
    return Path(path)


def _extended_windows_path(path: Path) -> Path:
    ordinary = str(_ordinary_windows_path(path))
    if ordinary.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + ordinary[2:])
    return Path("\\\\?\\" + ordinary)


def _same_windows_path(left: Path, right: Path) -> bool:
    def _key(path: Path) -> str:
        return str(_ordinary_windows_path(path)).replace("/", "\\").rstrip("\\").casefold()

    return _key(left) == _key(right)


def _canonical_classified_path(path: Path) -> tuple[Path, Path]:
    """Resolve a classified path without allowing it to redirect elsewhere."""
    authority = Path(os.path.abspath(path))
    ensure_no_reparse_ancestors(authority)
    canonical = canonical_existing_path(authority)
    if not _same_windows_path(canonical, authority):
        raise UnsafeWindowsPathError(
            f"Windows cleanup path resolved away from its classified location: {authority}"
        )
    return authority, canonical


def _assert_classified_path_unchanged(authority: Path) -> None:
    ensure_no_reparse_ancestors(authority)
    current = canonical_existing_path(authority)
    if not _same_windows_path(current, authority):
        raise UnsafeWindowsPathError(
            f"Windows cleanup path resolved away from its classified location: {authority}"
        )


def _windows_handle_identity(
    handle: int,
    path: Path,
    *,
    allow_directory: bool = False,
) -> tuple[Path, int, int, int]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WindowsByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD

    information = _WindowsByHandleFileInformation()
    if not get_information(handle, ctypes.byref(information)):
        error_code = int(ctypes.get_last_error())  # type: ignore[attr-defined]
        raise OSError(error_code, f"could not inspect Windows file identity: {path}")
    if information.file_attributes & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
        raise UnsafeWindowsPathError(f"Windows lifecycle path is a reparse point: {path}")
    if information.file_attributes & _WINDOWS_FILE_ATTRIBUTE_DIRECTORY and not allow_directory:
        raise UnsafeWindowsPathError(f"Windows lifecycle lock is not a file: {path}")
    capacity = 512
    while True:
        final_path_buffer = ctypes.create_unicode_buffer(capacity)
        final_path_length = get_final_path(handle, final_path_buffer, capacity, 0)
        if final_path_length == 0:
            error_code = int(ctypes.get_last_error())  # type: ignore[attr-defined]
            raise OSError(error_code, f"could not resolve Windows file identity: {path}")
        if final_path_length < capacity:
            final_path = _ordinary_windows_path(final_path_buffer.value)
            break
        capacity = final_path_length + 1
    creation_filetime = (int(information.creation_time.dwHighDateTime) << 32) | int(
        information.creation_time.dwLowDateTime
    )
    file_index = (int(information.file_index_high) << 32) | int(information.file_index_low)
    return final_path, int(information.volume_serial_number), file_index, creation_filetime


def _close_windows_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _delete_windows_file_by_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    set_information = kernel32.SetFileInformationByHandle
    set_information.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    set_information.restype = wintypes.BOOL
    disposition = _WindowsFileDispositionInformation(1)
    if not set_information(
        handle,
        _WINDOWS_FILE_DISPOSITION_INFO,
        ctypes.byref(disposition),
        ctypes.sizeof(disposition),
    ):
        error_code = int(ctypes.get_last_error())  # type: ignore[attr-defined]
        raise OSError(error_code, "could not remove an uncommitted Windows cleanup lock")


def _windows_path_identity(
    path: Path, *, allow_directory: bool = False
) -> tuple[Path, int, int, int]:
    if os.name != "nt":
        volume_serial, file_index, creation_filetime = _fallback_file_identity(path)
        return path.resolve(strict=True), volume_serial, file_index, creation_filetime

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(_extended_windows_path(path)),
        0,
        _WINDOWS_FILE_SHARE_ALL,
        None,
        _WINDOWS_OPEN_EXISTING,
        _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS | _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        error_code = int(ctypes.get_last_error())  # type: ignore[attr-defined]
        raise OSError(error_code, f"could not inspect Windows file identity: {path}")
    try:
        return _windows_handle_identity(handle, path, allow_directory=allow_directory)
    finally:
        _close_windows_handle(handle)


def _cleanup_target(path: Path) -> dict[str, object]:
    absolute = Path(os.path.abspath(path))
    if not windows_path_exists(absolute):
        return {"path": str(absolute), "kind": "missing", "sha256": ""}
    authority, canonical = _canonical_classified_path(absolute)
    if canonical.is_file():
        final_path, volume_serial, file_index, creation_filetime = _windows_path_identity(canonical)
        if not _same_windows_path(final_path, authority):
            raise UnsafeWindowsPathError(
                f"Windows cleanup path resolved away from its classified location: {authority}"
            )
        digest = _sha256(canonical)
        _assert_classified_path_unchanged(authority)
        return {
            "path": str(authority),
            "kind": "file",
            "sha256": digest,
            "volume_serial_number": volume_serial,
            "file_index": file_index,
            "creation_filetime_utc": creation_filetime,
        }
    if canonical.is_dir():
        final_path, volume_serial, file_index, creation_filetime = _windows_path_identity(
            canonical,
            allow_directory=True,
        )
        if not _same_windows_path(final_path, authority):
            raise UnsafeWindowsPathError(
                f"Windows cleanup path resolved away from its classified location: {authority}"
            )
        ensure_tree_has_no_reparse_points(canonical)
        _assert_classified_path_unchanged(authority)
        return {
            "path": str(authority),
            "kind": "directory",
            "sha256": "",
            "volume_serial_number": volume_serial,
            "file_index": file_index,
            "creation_filetime_utc": creation_filetime,
        }
    raise UnsafeWindowsPathError(f"cleanup target has an unsupported file type: {path}")


def _cleanup_data_target(path: Path) -> dict[str, object]:
    target = _cleanup_target(path)
    if target["kind"] not in {"missing", "directory"}:
        raise UnsafeWindowsPathError(f"Windows cleanup data target is not a directory: {path}")
    return target


def _cleanup_lock(path: Path) -> _CleanupLockLease:
    absolute = Path(os.path.abspath(path))
    if os.name == "nt":
        absolute = _ordinary_windows_path(absolute)
    _, canonical_parent = _canonical_classified_path(absolute.parent)
    expected_parent = _ordinary_windows_path(canonical_parent)
    expected_candidate = expected_parent / absolute.name
    if os.name != "nt":
        created = False
        try:
            descriptor = os.open(absolute, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            created = True
        except FileExistsError:
            descriptor = os.open(absolute, os.O_RDWR)
        metadata = os.fstat(descriptor)
        lease = _CleanupLockLease(
            payload={
                "path": str(absolute.resolve(strict=True)),
                "volume_serial_number": int(metadata.st_dev) & 0xFFFFFFFF,
                "file_index": int(metadata.st_ino) & 0xFFFFFFFFFFFFFFFF,
                "creation_filetime_utc": (metadata.st_ctime_ns // 100 + _WINDOWS_EPOCH_FILETIME),
            },
            path=absolute,
            created=created,
            descriptor=descriptor,
        )
    else:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        create_file = kernel32.CreateFileW
        create_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        create_file.restype = wintypes.HANDLE
        ctypes.set_last_error(0)  # type: ignore[attr-defined]
        handle = create_file(
            str(_extended_windows_path(absolute)),
            _WINDOWS_GENERIC_READ | _WINDOWS_GENERIC_WRITE | _WINDOWS_DELETE,
            _WINDOWS_FILE_SHARE_ALL,
            None,
            _WINDOWS_OPEN_ALWAYS,
            _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            error_code = int(ctypes.get_last_error())  # type: ignore[attr-defined]
            raise OSError(error_code, f"could not open Windows cleanup lock: {path}")
        created = int(ctypes.get_last_error()) != _WINDOWS_ERROR_ALREADY_EXISTS  # type: ignore[attr-defined]
        try:
            final_path, volume_serial, file_index, creation_filetime = _windows_handle_identity(
                handle, absolute
            )
            lease = _CleanupLockLease(
                payload={
                    "path": str(final_path),
                    "volume_serial_number": volume_serial,
                    "file_index": file_index,
                    "creation_filetime_utc": creation_filetime,
                },
                path=absolute,
                created=created,
                native_handle=handle,
            )
        except Exception:
            failed_lease = _CleanupLockLease(
                payload={},
                path=absolute,
                created=created,
                native_handle=handle,
            )
            failed_lease.close()
            raise

    try:
        final_path = Path(str(lease.payload["path"]))
        if not _same_windows_path(final_path, expected_candidate):
            raise UnsafeWindowsPathError(
                "Windows cleanup lock resolved outside its install directory"
            )
        ensure_no_reparse_ancestors(absolute)
        current_parent = _ordinary_windows_path(canonical_existing_path(absolute.parent))
        current_candidate = current_parent / absolute.name
        if not _same_windows_path(final_path, current_candidate):
            raise UnsafeWindowsPathError(
                "Windows cleanup lock resolved outside its install directory"
            )
        payload_file_index = lease.payload["file_index"]
        payload_creation_filetime = lease.payload["creation_filetime_utc"]
        if (
            not isinstance(payload_file_index, int)
            or not isinstance(payload_creation_filetime, int)
            or payload_file_index == 0
            or payload_creation_filetime <= 0
        ):
            raise UnsafeWindowsPathError("Windows cleanup lock identity is invalid")
    except Exception:
        lease.close()
        raise
    return lease


def _parent_payload(identity: WindowsProcessIdentity) -> dict[str, object]:
    return {
        "pid": identity.pid,
        "path": str(identity.executable),
        "started_filetime_utc": identity.started_filetime_utc,
    }


def schedule_windows_cleanup(
    paths: list[Path],
    *,
    parent_pid: int,
    data_paths: list[Path] | None = None,
    install_lock_path: Path | None = None,
    data_guard_paths: list[Path] | None = None,
) -> tuple[bool, str | None]:
    """Schedule exact legacy targets, preserving replacements created before cleanup."""
    return _schedule_payload(
        paths,
        parent_pid=parent_pid,
        managed=None,
        data_paths=data_paths,
        install_lock_path=install_lock_path,
        data_guard_paths=data_guard_paths,
    )


def _schedule_payload(
    paths: list[Path],
    *,
    parent_pid: int,
    managed: dict[str, object] | None,
    data_paths: list[Path] | None = None,
    install_lock_path: Path | None = None,
    data_guard_paths: list[Path] | None = None,
    parent_identity: WindowsProcessIdentity | None = None,
) -> tuple[bool, str | None]:
    if parent_identity is None:
        parent_identity, identity_error = windows_process_identity(
            parent_pid, expected_executable=Path(sys.executable)
        )
        if identity_error is not None or parent_identity is None:
            return False, identity_error or "could not capture cleanup parent identity"
    if parent_identity.pid != parent_pid:
        return False, "cleanup parent identity did not match the requested PID"

    lock_lease: _CleanupLockLease | None = None
    try:
        target_records = [_cleanup_target(path) for path in paths]
        data_target_records = [_cleanup_data_target(path) for path in data_paths or []]
        if install_lock_path is not None:
            lock_lease = _cleanup_lock(install_lock_path)
    except (OSError, UnsafeWindowsPathError) as exc:
        return False, str(exc)

    try:
        payload_json = json.dumps(
            {
                "operation_id": uuid.uuid4().hex,
                "parent": _parent_payload(parent_identity),
                "targets": target_records,
                "managed": managed,
                "data_targets": data_target_records,
                "lock": lock_lease.payload if lock_lease is not None else None,
                "data_guard_paths": [
                    str(Path(os.path.abspath(path))) for path in data_guard_paths or []
                ],
            },
            ensure_ascii=True,
        )
        cleanup_payload = base64.b64encode(payload_json.encode("utf-8")).decode("ascii")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200
        )
        cleanup_fd, cleanup_name = tempfile.mkstemp(prefix="opensre-uninstall-", suffix=".ps1")
        cleanup_path = Path(cleanup_name)
        with os.fdopen(cleanup_fd, "w", encoding="utf-8-sig", newline="") as cleanup_file:
            cleanup_file.write(read_cleanup_script())

        try:
            subprocess.Popen(
                [
                    powershell.windows_powershell_executable(),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-WindowStyle",
                    "Hidden",
                    "-File",
                    str(cleanup_path),
                    "-ParentProcessId",
                    str(parent_pid),
                    "-CleanupPayload",
                    cleanup_payload,
                    "-CleanupScriptPath",
                    str(cleanup_path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                creationflags=creationflags,
                cwd=tempfile.gettempdir(),
                env=powershell.windows_powershell_environment(),
            )
        except OSError as exc:
            cleanup_path.unlink(missing_ok=True)
            return False, str(exc)
        if lock_lease is not None:
            lock_lease.committed = True
        return True, None
    finally:
        if lock_lease is not None:
            lock_lease.close()


def _managed_payload(
    *, executable: Path, app_root: Path, launcher: Path | None
) -> dict[str, object]:
    app_root_authority, canonical_app_root = _canonical_classified_path(app_root)
    executable_authority, canonical_executable = _canonical_classified_path(executable)
    active_version_authority, canonical_active_version = _canonical_classified_path(
        executable_authority.parent
    )
    ensure_tree_has_no_reparse_points(canonical_app_root)
    if not windows_path_is_within(canonical_app_root, canonical_executable):
        raise UnsafeWindowsPathError("managed executable escapes its application root")
    final_executable, _, _, _ = _windows_path_identity(canonical_executable)
    if not _same_windows_path(final_executable, executable_authority):
        raise UnsafeWindowsPathError(
            "Windows cleanup path resolved away from its classified location: "
            f"{executable_authority}"
        )
    final_app_root, app_volume_serial, app_file_index, app_creation_filetime = (
        _windows_path_identity(canonical_app_root, allow_directory=True)
    )
    if not _same_windows_path(final_app_root, app_root_authority):
        raise UnsafeWindowsPathError(
            f"Windows cleanup path resolved away from its classified location: {app_root_authority}"
        )
    (
        final_active_version,
        active_version_volume_serial,
        active_version_file_index,
        active_version_creation_filetime,
    ) = _windows_path_identity(canonical_active_version, allow_directory=True)
    if not _same_windows_path(final_active_version, active_version_authority):
        raise UnsafeWindowsPathError(
            "Windows cleanup path resolved away from its classified location: "
            f"{active_version_authority}"
        )
    executable_digest = _sha256(canonical_executable)
    app_created_filetime = _creation_filetime(canonical_app_root)
    launcher_target = _cleanup_target(launcher) if launcher is not None else None
    if launcher_target is not None and launcher_target["kind"] not in {"missing", "file"}:
        raise UnsafeWindowsPathError(
            f"Windows managed launcher is not a file: {launcher_target['path']}"
        )
    _assert_classified_path_unchanged(executable_authority)
    _assert_classified_path_unchanged(active_version_authority)
    _assert_classified_path_unchanged(app_root_authority)
    return {
        "active_version": str(executable_authority.parent),
        "active_executable_sha256": executable_digest,
        "app_created_filetime_utc": app_created_filetime,
        "app_root": str(app_root_authority),
        "app_target": {
            "path": str(app_root_authority),
            "kind": "directory",
            "sha256": "",
            "volume_serial_number": app_volume_serial,
            "file_index": app_file_index,
            "creation_filetime_utc": app_creation_filetime,
        },
        "expected_install_id": executable_authority.parent.name,
        "launcher": str(launcher_target["path"]) if launcher_target is not None else "",
        "launcher_target": launcher_target,
        "layout_marker_text": WINDOWS_LAYOUT_MARKER_TEXT,
        "active_version_target": {
            "path": str(active_version_authority),
            "kind": "directory",
            "sha256": "",
            "volume_serial_number": active_version_volume_serial,
            "file_index": active_version_file_index,
            "creation_filetime_utc": active_version_creation_filetime,
        },
        "lock_path": str(
            _ordinary_windows_path(app_root_authority.parent / WINDOWS_INSTALL_LOCK_FILENAME)
        ),
    }


def schedule_windows_managed_cleanup(
    *,
    executable: Path,
    app_root: Path,
    launcher: Path | None,
    parent_pid: int,
    data_paths: list[Path] | None = None,
) -> tuple[bool, str | None]:
    """Schedule removal of a proven managed layout after revalidating it under lock."""
    try:
        managed = _managed_payload(executable=executable, app_root=app_root, launcher=launcher)
    except (OSError, UnsafeWindowsPathError) as exc:
        return False, str(exc)
    install_dir = Path(str(managed["app_root"])).parent
    return _schedule_payload(
        [],
        parent_pid=parent_pid,
        managed=managed,
        data_paths=data_paths,
        install_lock_path=install_dir / WINDOWS_INSTALL_LOCK_FILENAME,
        data_guard_paths=[
            install_dir / WINDOWS_BINARY_FILENAME,
            install_dir / WINDOWS_LAUNCHER_FILENAME,
        ],
    )
