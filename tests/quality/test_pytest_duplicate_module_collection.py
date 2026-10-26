"""Duplicate test basenames must collect in one non-xdist session.

pytest's default prepend import mode imports files as the basename module, so
``tests/filestorage/test_enums.py`` and ``tests/core/tool/test_enums.py`` both
become ``test_enums`` and collection fails with ``import file mismatch``.
``pytest.ini`` sets ``--import-mode=importlib`` so each file loads uniquely
(issue #5325).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTEST_INI = _REPO_ROOT / "pytest.ini"
_COLLIDING_ENUM_TESTS = (
    "tests/filestorage/test_enums.py",
    "tests/core/tool/test_enums.py",
)


def test_pytest_ini_uses_importlib_import_mode() -> None:
    text = _PYTEST_INI.read_text(encoding="utf-8")
    assert "--import-mode=importlib" in text


def test_duplicate_test_enums_modules_collect_together() -> None:
    env = os.environ.copy()
    env.pop("PYTEST_ADDOPTS", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            *_COLLIDING_ENUM_TESTS,
            "--collect-only",
            "-q",
            "-p",
            "no:xdist",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = f"{result.stdout}{result.stderr}"
    assert result.returncode == 0, combined
    assert "import file mismatch" not in combined
