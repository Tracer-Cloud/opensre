"""Release-artifact smoke contract for dynamically bundled code and data."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from bootstrap.frozen_ca_bundle import use_bundled_ca_certificates
from config.constants import SSL_CERT_DIR_ENV, SSL_CERT_FILE_ENV
from surfaces.cli.app import cli
from tools.registry_index import BAKED_INDEX_RELATIVE_PATH


def _boot_frozen_ca(monkeypatch: pytest.MonkeyPatch) -> None:
    """What a frozen binary's boot does before any command: fall back to certifi if needed."""
    for name in (SSL_CERT_FILE_ENV, SSL_CERT_DIR_ENV):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    use_bundled_ca_certificates()


def test_package_smoke_finds_essential_tools_and_skills() -> None:
    result = CliRunner().invoke(cli, ["_package-smoke"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    # Catalog size from the descriptor index (no full vendor import).
    assert payload["registered_tools"] >= 250
    # Deep-checked essentials only (required name set).
    assert payload["checked_tools"] == 7
    assert payload["action_skills"] >= 5
    assert payload["integration_verifiers"] >= 60
    assert "planning_instructions" not in payload


def test_package_smoke_command_is_hidden_from_help() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0, result.output
    assert "_package-smoke" not in result.output


def test_package_smoke_fails_when_frozen_bundle_lacks_baked_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Release smoke must not pass on a frozen artifact that fell back to the slow path."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    result = CliRunner().invoke(cli, ["_package-smoke"])

    assert result.exit_code != 0, result.output
    assert BAKED_INDEX_RELATIVE_PATH.as_posix() in result.output
    assert "missing_baked_descriptor_index" in result.output


def test_package_smoke_reports_baked_index_on_frozen_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frozen smoke must prove it loaded the bake, not the every-vendor fallback."""
    from tools.registry_index import clear_descriptor_index_cache, dump_descriptor_index

    dump_descriptor_index(tmp_path / BAKED_INDEX_RELATIVE_PATH)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    _boot_frozen_ca(monkeypatch)
    clear_descriptor_index_cache()
    try:
        result = CliRunner().invoke(cli, ["_package-smoke"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["status"] == "ok"
        assert payload["baked_descriptor_index"] is True
        assert payload["registered_tools"] >= 250
    finally:
        clear_descriptor_index_cache()


def _first_certificate() -> str:
    import certifi

    marker = "-----END CERTIFICATE-----"
    return Path(certifi.where()).read_text(encoding="utf-8").split(marker, 1)[0] + marker + "\n"


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows loads its system store whatever OpenSSL's paths say"
)
@pytest.mark.parametrize(
    ("ca_directory", "trusted"),
    [("missing", False), ("empty", False), ("hashed", True)],
)
def test_frozen_smoke_requires_a_ca_the_tls_context_can_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ca_directory: str, trusted: bool
) -> None:
    """Release smoke must fail an artifact whose OpenSSL trusts no CA (every ``urllib`` HTTPS
    call fails), yet pass one that trusts a hashed CA directory, which OpenSSL reads only
    while verifying, so it never shows in the loaded count."""
    from tools.registry_index import clear_descriptor_index_cache, dump_descriptor_index

    dump_descriptor_index(tmp_path / BAKED_INDEX_RELATIVE_PATH)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    # What the bundled libcrypto sees: no CA file, and the directory under test.
    directory = tmp_path / "certs"
    if ca_directory != "missing":
        directory.mkdir()
    if ca_directory == "hashed":
        (directory / "00000000.0").write_text(_first_certificate(), encoding="utf-8")
    monkeypatch.setenv(SSL_CERT_FILE_ENV, str(tmp_path / "missing" / "cert.pem"))
    monkeypatch.setenv(SSL_CERT_DIR_ENV, str(directory))
    clear_descriptor_index_cache()
    try:
        result = CliRunner().invoke(cli, ["_package-smoke"])
    finally:
        clear_descriptor_index_cache()

    assert (result.exit_code == 0) is trusted, result.output
    assert ("missing_ca_certificates" in result.output) is not trusted
