"""Parse a YAML, JSON or TOML file and describe one part of its structure.

Counting by hand from shell output is where wrong numbers come from: a pattern
over indentation also matches nested keys, and a range read drops what follows.
Loading the file answers the question directly.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: Extensions this module knows how to load.
FORMAT_BY_SUFFIX: dict[str, str] = {
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
}
#: Keys listed back to the caller; a longer mapping reports its size only.
MAX_LISTED_KEYS = 60


@dataclass(frozen=True)
class StructureView:
    """What one path inside a parsed file contains."""

    path: str
    file_format: str
    key: str
    kind: str
    count: int | None
    keys: tuple[str, ...]
    value: str | None


class StructureError(ValueError):
    """The file could not be read, parsed, or does not contain the key."""


def load_structured_file(path: Path) -> Any:
    """Return the parsed document, chosen by suffix."""
    file_format = FORMAT_BY_SUFFIX.get(path.suffix.lower())
    if file_format is None:
        supported = ", ".join(sorted(set(FORMAT_BY_SUFFIX)))
        raise StructureError(f"{path.suffix or path.name} is not one of {supported}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise StructureError(f"cannot read {path}: {type(exc).__name__}") from exc
    try:
        if file_format == "yaml":
            return yaml.safe_load(raw.decode("utf-8"))
        if file_format == "json":
            return json.loads(raw)
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise StructureError(f"{path} is not valid {file_format}: {type(exc).__name__}") from exc


#: Words YAML 1.1 reads as booleans, so ``on:`` in a workflow is the key ``True``.
_YAML_WORD_KEYS: dict[str, bool] = {
    "on": True,
    "off": False,
    "yes": True,
    "no": False,
    "true": True,
    "false": False,
}


def _member(mapping: dict[Any, Any], name: str) -> tuple[Any, bool]:
    """Fetch ``name`` from ``mapping``, allowing for YAML's boolean spellings."""
    if name in mapping:
        return mapping[name], True
    spelled = _YAML_WORD_KEYS.get(name.lower())
    if spelled is not None and spelled in mapping:
        return mapping[spelled], True
    return None, False


def resolve_key(document: Any, key: str) -> Any:
    """Walk a dotted ``key`` into ``document``; ``""`` is the whole document.

    A mapping key that is itself dotted (``on.push``) is matched before the dot
    is treated as a separator, so real key names win over the path syntax.
    """
    if not key:
        return document
    current = document
    remaining = key
    while remaining:
        if isinstance(current, dict):
            whole, found = _member(current, remaining)
            if found:
                return whole
        head, _, remaining_tail = remaining.partition(".")
        if not isinstance(current, dict):
            raise StructureError(f"{key!r} is not in this file")
        current, found = _member(current, head)
        if not found:
            raise StructureError(f"{key!r} is not in this file")
        remaining = remaining_tail
    return current


def describe(path: Path, key: str = "") -> StructureView:
    """Describe what sits at ``key`` inside ``path``."""
    document = load_structured_file(path)
    target = resolve_key(document, key)
    file_format = FORMAT_BY_SUFFIX[path.suffix.lower()]
    if isinstance(target, dict):
        names = [str(name) for name in target]
        return StructureView(
            path=str(path),
            file_format=file_format,
            key=key,
            kind="mapping",
            count=len(names),
            keys=tuple(names[:MAX_LISTED_KEYS]),
            value=None,
        )
    if isinstance(target, list):
        return StructureView(
            path=str(path),
            file_format=file_format,
            key=key,
            kind="list",
            count=len(target),
            keys=(),
            value=None,
        )
    return StructureView(
        path=str(path),
        file_format=file_format,
        key=key,
        kind=type(target).__name__,
        count=None,
        keys=(),
        value=str(target),
    )


__all__ = [
    "FORMAT_BY_SUFFIX",
    "MAX_LISTED_KEYS",
    "StructureError",
    "StructureView",
    "describe",
    "load_structured_file",
    "resolve_key",
]
