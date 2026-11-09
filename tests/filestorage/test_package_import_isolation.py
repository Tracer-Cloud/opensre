"""The filestorage package facade must not load the sync engine at import."""

from __future__ import annotations

import subprocess
import sys


def test_importing_the_package_does_not_load_the_sync_engine() -> None:
    probe = (
        "import sys; import infrastructure.filestorage as fs; "
        "print('ENGINE', 'infrastructure.filestorage.engine' in sys.modules); "
        "print('OPS', 'infrastructure.filestorage.operations' in sys.modules); "
        "print('ERROR', fs.RemoteSyncError.__name__)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ENGINE False" in result.stdout, result.stdout + result.stderr
    assert "OPS False" in result.stdout, result.stdout + result.stderr
    assert "ERROR RemoteSyncError" in result.stdout, result.stdout + result.stderr
