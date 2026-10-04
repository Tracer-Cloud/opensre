"""The frozen binary's runtime hook that gives its bundled OpenSSL CA certificates."""

from __future__ import annotations

import os
import ssl
from pathlib import Path
from typing import Any

import certifi
import pytest

from infrastructure.deployment.packaging.pyi_rth_ca_bundle import use_bundled_ca_certificates

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOK = "infrastructure/deployment/packaging/pyi_rth_ca_bundle.py"


def _verify_paths(cafile: str | None, capath: str | None) -> Any:
    def paths() -> ssl.DefaultVerifyPaths:
        return ssl.DefaultVerifyPaths(
            cafile, capath, "SSL_CERT_FILE", "/build/ssl/cert.pem", "SSL_CERT_DIR", "/build/ssl"
        )

    return paths


@pytest.fixture
def _no_ca_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither variable set, and whatever the hook sets is undone after the test."""
    for name in ("SSL_CERT_FILE", "SSL_CERT_DIR"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


@pytest.mark.usefixtures("_no_ca_env")
def test_an_openssl_whose_ca_locations_are_missing_here_gets_the_bundled_certificates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the macOS binary bundled a libcrypto whose compiled-in CA directory exists
    only on the machine that built it, so every ``urllib`` HTTPS call (the GitHub REST client)
    failed with CERTIFICATE_VERIFY_FAILED while httpx, which passes certifi, kept working."""
    monkeypatch.setattr(ssl, "get_default_verify_paths", _verify_paths(None, None))

    bundle = use_bundled_ca_certificates()

    assert bundle == certifi.where()
    assert os.environ["SSL_CERT_FILE"] == bundle


@pytest.mark.usefixtures("_no_ca_env")
@pytest.mark.parametrize("variable", ["SSL_CERT_FILE", "SSL_CERT_DIR"])
def test_a_ca_location_the_user_chose_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, variable: str
) -> None:
    """A network that inspects TLS needs its own CA bundle; the hook must not replace it."""
    monkeypatch.setattr(ssl, "get_default_verify_paths", _verify_paths(None, None))
    monkeypatch.setenv(variable, str(tmp_path / "corporate-ca.pem"))

    assert use_bundled_ca_certificates() is None
    assert os.environ[variable] == str(tmp_path / "corporate-ca.pem")


@pytest.mark.usefixtures("_no_ca_env")
def test_default_ca_locations_that_exist_here_are_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A system store that resolves (Debian's /usr/lib/ssl) may carry roots certifi lacks."""
    system_bundle = tmp_path / "cert.pem"
    system_bundle.write_text("", encoding="utf-8")
    monkeypatch.setattr(ssl, "get_default_verify_paths", _verify_paths(str(system_bundle), None))

    assert use_bundled_ca_certificates() is None
    assert "SSL_CERT_FILE" not in os.environ


def test_release_spec_runs_the_hook_before_the_application() -> None:
    spec = (_REPO_ROOT / "opensre.spec").read_text(encoding="utf-8")

    assert f'runtime_hooks=[str(ROOT / "{_HOOK}")]' in spec
    assert (_REPO_ROOT / _HOOK).is_file()
