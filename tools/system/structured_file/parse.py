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

from tools.system.workspace_paths import (
    WorkspacePathError,
    resolve_within_workspace,
    workspace_relative,
)

#: Extensions this module knows how to load.
FORMAT_BY_SUFFIX: dict[str, str] = {
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
}
#: Keys listed back to the caller; a longer mapping reports its size only.
MAX_LISTED_KEYS = 60


class KeysAsWritten(yaml.SafeLoader):
    """``SafeLoader`` that keeps mapping keys as the text the file shows.

    YAML 1.1 resolves bare ``on``, ``off``, ``yes`` and ``no`` to booleans, so a
    workflow's ``on:`` would be reported as ``True``, and two keys spelled
    differently could collapse into one and undercount the mapping. Values keep
    their normal types.
    """

    #: Keys YAML resolves to booleans; kept as the words the file spells.
    _BOOL_TAG = "tag:yaml.org,2002:bool"
    #: ``<<`` pulls an anchored mapping in; PyYAML normally expands it for us.
    _MERGE_TAG = "tag:yaml.org,2002:merge"

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        """Build the mapping, expanding ``<<`` merges and keeping keys as written.

        ``SafeConstructor.construct_mapping`` normally expands ``<<`` for us;
        overriding it means doing that here, or an anchored document fails to
        load at all.
        """
        self.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = (
                key_node.value
                if key_node.tag == self._BOOL_TAG
                else self.construct_object(key_node, deep=deep)
            )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


@dataclass(frozen=True)
class StructureView:
    """What one path inside a parsed file contains."""

    path: str
    file_format: str
    key: str
    kind: str
    count: int | None
    keys: tuple[str, ...]


class StructureError(ValueError):
    """The file could not be read, parsed, or does not contain the key."""


def load_structured_file(path: Path) -> Any:
    """Return the parsed document, chosen by suffix."""
    try:
        path = resolve_within_workspace(path)
    except WorkspacePathError as exc:
        raise StructureError(str(exc)) from exc
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
            return yaml.load(raw.decode("utf-8"), Loader=KeysAsWritten)  # noqa: S506
        if file_format == "json":
            return json.loads(raw)
        return tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
        raise StructureError(f"{path} is not valid {file_format}: {type(exc).__name__}") from exc


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
        if isinstance(current, dict) and remaining in current:
            return current[remaining]
        head, _, remaining_tail = remaining.partition(".")
        if not isinstance(current, dict) or head not in current:
            raise StructureError(f"{key!r} is not in this file")
        current = current[head]
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
            path=workspace_relative(path),
            file_format=file_format,
            key=key,
            kind="mapping",
            count=len(names),
            keys=tuple(names[:MAX_LISTED_KEYS]),
        )
    if isinstance(target, list):
        return StructureView(
            path=workspace_relative(path),
            file_format=file_format,
            key=key,
            kind="list",
            count=len(target),
            keys=(),
        )
    return StructureView(
        path=workspace_relative(path),
        file_format=file_format,
        key=key,
        # A scalar's contents are never returned: a config file may hold a
        # token, and this tool exists to describe structure.
        kind=type(target).__name__,
        count=None,
        keys=(),
    )


__all__ = [
    "FORMAT_BY_SUFFIX",
    "KeysAsWritten",
    "MAX_LISTED_KEYS",
    "StructureError",
    "StructureView",
    "describe",
    "load_structured_file",
    "resolve_key",
]
