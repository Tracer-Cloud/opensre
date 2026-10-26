"""Guard: the harness boot path imports without POSIX-only stdlib modules.

Frozen Windows builds have no ``resource`` / ``fcntl`` / ``termios`` / ``pwd`` /
``grp``. A bare top-level import of one anywhere the startup import chain reaches
crashes ``opensre.exe`` before it starts. This reproduces the Windows condition
in a subprocess (the modules are made unimportable) and asserts the boot chain
run by ``__main__`` still imports and installs cleanly.

Linux/macOS CI shards cannot catch this on their own — the modules are present
there — so this test simulates their absence.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

# POSIX-only stdlib modules absent on Windows.
_POSIX_ONLY_MODULES = ("resource", "fcntl", "termios", "pwd", "grp")

# The exact boot chain __main__ runs at startup (see surfaces .../boundary.py).
_BOOT_CALL = (
    "from surfaces.shared.terminal.output.boundary import install_harness_providers\n"
    "install_harness_providers()"
)


def test_harness_boot_imports_without_posix_only_modules() -> None:
    script = textwrap.dedent(
        """
        import builtins

        _blocked = set({blocked!r})
        _real_import = builtins.__import__

        def _guard(name, globals=None, locals=None, fromlist=(), level=0):
            # Absolute imports only. Relative imports (level > 0) reuse short
            # names — e.g. oauthlib's ``from .resource import …`` — which are
            # package modules, not the POSIX stdlib ``resource``.
            if level == 0 and name.split(".", 1)[0] in _blocked:
                raise ModuleNotFoundError(
                    f"No module named {{name!r}} (simulated Windows)"
                )
            return _real_import(name, globals, locals, fromlist, level)

        builtins.__import__ = _guard
        {boot_call}
        print("BOOT_OK")
        """
    ).format(blocked=list(_POSIX_ONLY_MODULES), boot_call=_BOOT_CALL)

    # A subprocess inherits os.environ but not this process's runtime sys.path
    # (e.g. a conftest injection), so pass the current import roots through
    # PYTHONPATH — otherwise 'surfaces' may be unimportable and the failure would
    # look like a POSIX-only-module problem when it is really a path problem.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, (
        f"harness boot subprocess exited {result.returncode} with POSIX-only "
        f"modules {_POSIX_ONLY_MODULES} absent (Windows):\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    assert "BOOT_OK" in result.stdout, (
        f"boot did not reach BOOT_OK:\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
