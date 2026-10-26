"""A frozen binary's bundled OpenSSL gets CA certificates, without overriding the user's."""

from __future__ import annotations

import os
import ssl
import sys
from pathlib import Path
from typing import Any

import certifi
import pytest

from bootstrap.frozen_ca_bundle import use_bundled_ca_certificates
from bootstrap.process import BootStep, ProcessName, ProcessProfile, configure_process
from config.constants import SSL_CERT_DIR_ENV, SSL_CERT_FILE_ENV
from config.local_env import OPENSRE_PROJECT_ENV_PATH_ENV, bootstrap_opensre_env

_ENV_ONLY = ProcessProfile(name=ProcessName.EMBEDDED, steps=frozenset({BootStep.ENV}))


def _verify_paths(cafile: str | None, capath: str | None) -> Any:
    def paths() -> ssl.DefaultVerifyPaths:
        return ssl.DefaultVerifyPaths(
            cafile, capath, SSL_CERT_FILE_ENV, "/build/ssl/cert.pem", SSL_CERT_DIR_ENV, "/build/ssl"
        )

    return paths


@pytest.fixture
def _frozen_without_ca_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A frozen process with neither variable set; whatever boot sets is undone afterwards."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    for name in (SSL_CERT_FILE_ENV, SSL_CERT_DIR_ENV):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


@pytest.mark.usefixtures("_frozen_without_ca_env")
@pytest.mark.parametrize(
    "ca_directory", [False, True], ids=["no-ca-directory", "empty-ca-directory"]
)
def test_an_openssl_without_a_ca_file_here_gets_the_bundled_certificates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ca_directory: bool
) -> None:
    """Regression: the macOS binary bundled a libcrypto whose compiled-in CA locations exist
    only where it was built, so every ``urllib`` HTTPS call (the GitHub REST client) failed
    with CERTIFICATE_VERIFY_FAILED. A CA directory that exists here but holds no
    certificates must not stop the fallback either."""
    directory = str(tmp_path) if ca_directory else None
    monkeypatch.setattr(ssl, "get_default_verify_paths", _verify_paths(None, directory))

    assert use_bundled_ca_certificates() == certifi.where()
    assert os.environ[SSL_CERT_FILE_ENV] == certifi.where()
    assert SSL_CERT_DIR_ENV not in os.environ


@pytest.mark.usefixtures("_frozen_without_ca_env")
def test_a_ca_bundle_set_in_the_env_file_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: set before ``~/.opensre/.env`` loaded, certifi won over a corporate CA
    configured there, because loading an env file never replaces a variable already set."""
    corporate = tmp_path / "corporate-ca.pem"
    env_file = tmp_path / ".env"
    env_file.write_text(f"{SSL_CERT_FILE_ENV}={corporate}\n", encoding="utf-8")
    monkeypatch.setenv(OPENSRE_PROJECT_ENV_PATH_ENV, str(env_file))
    monkeypatch.setattr("bootstrap.process.bootstrap_opensre_env_once", bootstrap_opensre_env)
    monkeypatch.setattr(ssl, "get_default_verify_paths", _verify_paths(None, None))

    configure_process(_ENV_ONLY)

    assert os.environ[SSL_CERT_FILE_ENV] == str(corporate)


@pytest.mark.usefixtures("_frozen_without_ca_env")
def test_a_compiled_in_ca_file_missing_on_this_machine_gets_the_bundled_certificates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regression: Homebrew's libssl reports ``/opt/homebrew/etc/openssl@3/cert.pem`` even
    on a Mac where that file was never installed. The path is non-empty, so boot left
    ``urllib`` (the GitHub REST client) with an empty trust store and GitHub Actions
    reads failed ``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``."""
    missing = tmp_path / "openssl@3" / "cert.pem"
    monkeypatch.setattr(ssl, "get_default_verify_paths", _verify_paths(str(missing), None))

    assert use_bundled_ca_certificates() == certifi.where()
    assert os.environ[SSL_CERT_FILE_ENV] == certifi.where()
    assert ssl.create_default_context().cert_store_stats()["x509_ca"] > 0


@pytest.mark.usefixtures("_frozen_without_ca_env")
def test_a_default_ca_file_that_exists_here_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A system bundle that resolves (Debian's /usr/lib/ssl) may carry roots certifi lacks."""
    system_bundle = tmp_path / "cert.pem"
    system_bundle.write_text("", encoding="utf-8")
    monkeypatch.setattr(ssl, "get_default_verify_paths", _verify_paths(str(system_bundle), None))

    assert use_bundled_ca_certificates() is None
    assert SSL_CERT_FILE_ENV not in os.environ
