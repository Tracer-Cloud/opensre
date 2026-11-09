"""Sign skills releases the way the OpenSRE app does, for offline tests."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from config.constants.skills import (
    SKILLS_API_VERSION,
    SKILLS_AUTO_UPDATE_ENV,
    SKILLS_TRUSTED_KEYS_FILE_ENV,
)
from core.agent_harness.prompts.skills import active_skill_catalog, skills_dir
from core.agent_harness.prompts.skills.snapshot import (
    SkillsRelease,
    forget_rejections,
    is_release_path,
    signing_message,
)


@dataclass(frozen=True)
class ReleaseSigner:
    """A trusted P-256 key and the id releases name it by."""

    key: ec.EllipticCurvePrivateKey
    key_id: str = "test-1"

    def sign(
        self, files: Mapping[str, str], *, seq: int, skill_api: int = SKILLS_API_VERSION
    ) -> SkillsRelease:
        signature = self.key.sign(signing_message(seq, skill_api, files), ec.ECDSA(hashes.SHA256()))
        return SkillsRelease(
            seq=seq,
            skill_api=skill_api,
            files=dict(files),
            key_id=self.key_id,
            signature=base64.b64encode(signature).decode("ascii"),
            source="cli",
        )

    def public_pem(self) -> str:
        return (
            self.key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode("ascii")
        )


def bundled_files() -> dict[str, str]:
    """The bundled catalog as a release ``files`` map."""
    root = skills_dir()
    return {
        relative: path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and "__pycache__" not in path.parts
        and is_release_path(relative := path.relative_to(root).as_posix())
    }


@pytest.fixture
def release_signer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[ReleaseSigner]:
    """Trust a fresh key and let this process activate stored releases."""
    signer = ReleaseSigner(ec.generate_private_key(ec.SECP256R1()))
    keys = tmp_path / "trusted-keys.json"
    keys.write_text(json.dumps({signer.key_id: signer.public_pem()}), encoding="utf-8")
    monkeypatch.setenv(SKILLS_TRUSTED_KEYS_FILE_ENV, str(keys))
    monkeypatch.setenv(SKILLS_AUTO_UPDATE_ENV, "1")
    forget_rejections()
    active_skill_catalog().invalidate()
    yield signer
    forget_rejections()
    active_skill_catalog().invalidate()
