"""Signed skills releases: the wire document, its signature, and its files on disk.

The OpenSRE app signs ``signing_message`` with ECDSA P-256 / SHA-256 (DER,
base64). The message commits to the sequence number, the card contract version
and every file's path and content, so a release cannot be altered or replayed
under another sequence without breaking the signature.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from config.constants.skills import (
    SKILLS_RELEASE_MAX_BYTES,
    SKILLS_RELEASE_MAX_FILES,
    SKILLS_RELEASE_PUBLIC_KEYS,
    SKILLS_TRUSTED_KEYS_FILE_ENV,
)

_MESSAGE_PREFIX = "opensre-skills-release/v1"
_PATH_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)*$")
_SCRIPT_RE = re.compile(r"(?:^|/)scripts/[a-z_][a-z0-9_]*\.py$")
_MAX_PATH_LENGTH = 200
_EXCLUDED_TOP_LEVEL = frozenset({"AGENTS.md", "README.md"})
_EXCLUDED_PREFIXES = ("_template/",)


class ReleaseError(ValueError):
    """A release document is malformed, untrusted, or unusable by this binary."""


@dataclass(frozen=True)
class SkillsRelease:
    """One immutable catalog release as served by ``GET /api/skills/release``."""

    seq: int
    skill_api: int
    files: Mapping[str, str]
    key_id: str
    signature: str
    source: str = ""
    source_ref: str = ""
    created_at: str = ""

    @classmethod
    def from_document(cls, document: Any) -> SkillsRelease:
        """Parse the wire document, rejecting anything not shaped like a release."""
        if not isinstance(document, Mapping):
            raise ReleaseError("release document must be an object")
        seq = document.get("seq")
        skill_api = document.get("skill_api")
        files = document.get("files")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            raise ReleaseError("release seq must be a positive integer")
        if not isinstance(skill_api, int) or isinstance(skill_api, bool) or skill_api < 1:
            raise ReleaseError("release skill_api must be a positive integer")
        if not isinstance(files, Mapping) or not all(
            isinstance(path, str) and isinstance(text, str) for path, text in files.items()
        ):
            raise ReleaseError("release files must map paths to text")
        text_fields = {
            name: document.get(name, "")
            for name in ("key_id", "signature", "source", "source_ref", "created_at")
        }
        if not all(isinstance(value, str) for value in text_fields.values()):
            raise ReleaseError("release metadata fields must be strings")
        return cls(
            seq=seq,
            skill_api=skill_api,
            files=MappingProxyType(dict(files)),
            **text_fields,
        )

    def to_document(self) -> dict[str, Any]:
        """Return the wire document (round-trips through :meth:`from_document`)."""
        return {
            "seq": self.seq,
            "skill_api": self.skill_api,
            "files": dict(self.files),
            "key_id": self.key_id,
            "signature": self.signature,
            "source": self.source,
            "source_ref": self.source_ref,
            "created_at": self.created_at,
        }


def tree_digest(files: Mapping[str, str]) -> str:
    """Return the sha256 over sorted ``path``/content-hash lines (the signed tree)."""
    lines = "".join(
        f"{path}\n{hashlib.sha256(files[path].encode('utf-8')).hexdigest()}\n"
        for path in sorted(files)
    )
    return hashlib.sha256(lines.encode("utf-8")).hexdigest()


def signing_message(seq: int, skill_api: int, files: Mapping[str, str]) -> bytes:
    """Return the exact bytes the server signs for a release."""
    return (
        f"{_MESSAGE_PREFIX}\nseq={seq}\nskill_api={skill_api}\ntree={tree_digest(files)}\n"
    ).encode()


def is_release_path(path: str) -> bool:
    """True when ``path`` (root-relative, ``/``-separated) is catalog content a release ships."""
    if len(path) > _MAX_PATH_LENGTH or not _PATH_RE.match(path):
        return False
    if path in _EXCLUDED_TOP_LEVEL or path.startswith(_EXCLUDED_PREFIXES):
        return False
    return path.endswith(".md") or bool(_SCRIPT_RE.search(path))


def release_path_problems(files: Mapping[str, str]) -> list[str]:
    """Return every reason ``files`` is not a shippable catalog (empty when valid)."""
    problems: list[str] = []
    total = 0
    if len(files) > SKILLS_RELEASE_MAX_FILES:
        problems.append(f"too many files ({len(files)} > {SKILLS_RELEASE_MAX_FILES})")
    for path, text in files.items():
        if len(path) > _MAX_PATH_LENGTH or not _PATH_RE.match(path):
            problems.append(f"{path!r}: unsafe path")
            continue
        if path in _EXCLUDED_TOP_LEVEL or path.startswith(_EXCLUDED_PREFIXES):
            problems.append(f"{path!r}: not shipped in a release")
        elif not (path.endswith(".md") or _SCRIPT_RE.search(path)):
            problems.append(f"{path!r}: only Markdown and scripts/*.py may ship")
        if "\x00" in text:
            problems.append(f"{path!r}: contains a NUL character")
        total += len(text.encode("utf-8", errors="surrogatepass"))
    if total > SKILLS_RELEASE_MAX_BYTES:
        problems.append(f"release is {total} bytes (max {SKILLS_RELEASE_MAX_BYTES})")
    if not any(path == "SKILL.md" or path.endswith("/SKILL.md") for path in files):
        problems.append("release has no SKILL.md")
    return problems


def trusted_release_keys() -> Mapping[str, str]:
    """Return the keys this process trusts: compiled-in, plus a dev file outside releases."""
    keys = dict(SKILLS_RELEASE_PUBLIC_KEYS)
    path = os.getenv(SKILLS_TRUSTED_KEYS_FILE_ENV, "").strip()
    if path and not getattr(sys, "frozen", False):
        try:
            extra = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ReleaseError(f"cannot read {SKILLS_TRUSTED_KEYS_FILE_ENV}: {exc}") from exc
        if isinstance(extra, dict):
            keys.update({str(key): str(value) for key, value in extra.items()})
    return keys


def verify_release(release: SkillsRelease, keys: Mapping[str, str]) -> None:
    """Raise :class:`ReleaseError` unless a trusted key signed exactly this release."""
    pem = keys.get(release.key_id)
    if not pem:
        raise ReleaseError(f"release {release.seq} is signed by untrusted key {release.key_id!r}")
    try:
        public_key = load_pem_public_key(pem.encode("utf-8"))
        signature = base64.b64decode(release.signature, validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ReleaseError(f"release {release.seq} has an unreadable key or signature") from exc
    if not isinstance(public_key, ec.EllipticCurvePublicKey):
        raise ReleaseError(f"trusted key {release.key_id!r} is not an EC key")
    message = signing_message(release.seq, release.skill_api, release.files)
    try:
        public_key.verify(signature, message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature as exc:
        raise ReleaseError(f"release {release.seq} signature does not verify") from exc
    problems = release_path_problems(release.files)
    if problems:
        raise ReleaseError(f"release {release.seq} is not shippable: {'; '.join(problems[:3])}")


def materialize_release(release: SkillsRelease, directory: Path) -> Path:
    """Write the release's files under the new, empty ``directory`` and return it.

    Callers verify first; paths are re-checked here so a file can never land
    outside ``directory``.
    """
    directory.mkdir(parents=True, exist_ok=False, mode=0o755)
    base = directory.resolve()
    for relative, text in release.files.items():
        if not _PATH_RE.match(relative):
            raise ReleaseError(f"{relative!r}: unsafe path")
        target = base.joinpath(*relative.split("/"))
        if base not in target.parents:
            raise ReleaseError(f"{relative!r}: escapes the release directory")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        with target.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
        target.chmod(0o644)
    return directory


__all__ = [
    "ReleaseError",
    "SkillsRelease",
    "is_release_path",
    "materialize_release",
    "release_path_problems",
    "signing_message",
    "trusted_release_keys",
    "tree_digest",
    "verify_release",
]
