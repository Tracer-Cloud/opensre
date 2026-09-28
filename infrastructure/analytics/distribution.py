"""Classify the code actually loaded, independently of where it runs."""

from __future__ import annotations

import importlib.metadata
import json
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


def _editable_root(distribution: importlib.metadata.Distribution) -> Path | None:
    direct_url = distribution.read_text("direct_url.json")
    if not direct_url:
        return None
    try:
        metadata = json.loads(direct_url)
    except ValueError:
        return None
    if not isinstance(metadata, dict) or not isinstance(metadata.get("dir_info"), dict):
        return None
    if metadata["dir_info"].get("editable") is not True:
        return None
    url = metadata.get("url")
    if not isinstance(url, str):
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        return None
    return Path(url2pathname(parsed.path)).resolve()


def detect_distribution() -> str:
    """Return loaded-code distribution; packaged code is not proof of publisher signing."""
    if getattr(sys, "frozen", False) or getattr(sys, "_MEIPASS", None):
        return "frozen_binary"

    code_file = Path(__file__).resolve()
    code_root = code_file.parents[2]
    try:
        distribution = importlib.metadata.distribution("opensre")
    except (importlib.metadata.PackageNotFoundError, OSError, ValueError):
        distribution = None

    try:
        if distribution is not None and _editable_root(distribution) == code_root:
            return "editable_package"
        # Inspect only the loaded package root: a wheel in a checkout's .venv
        # must not inherit the ancestor repository's development classification.
        pyproject = code_root / "pyproject.toml"
        if pyproject.is_file():
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
            if isinstance(project, dict) and project.get("name") == "opensre":
                return "source_checkout"
        if distribution is not None and any(
            Path(str(distribution.locate_file(file))).resolve() == code_file
            for file in distribution.files or ()
            if file.as_posix() == code_file.relative_to(code_root).as_posix()
        ):
            return "installed_package"
    except (OSError, ValueError):
        return "unknown"
    return "unknown"
