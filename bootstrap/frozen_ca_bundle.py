"""Give a frozen binary's bundled OpenSSL CA certificates it can find.

OpenSSL looks for its default CA certificates in a directory compiled into the
library. The binary bundles a libcrypto built on another machine (the one a
dependency's wheel shipped), so on a user's machine its CA file does not exist
and every client relying on the defaults, ``urllib`` among them, fails
certificate verification. httpx and requests pass certifi's bundle explicitly
and are unaffected.
"""

from __future__ import annotations

import os
import ssl
import sys

from config.constants import SSL_CERT_DIR_ENV, SSL_CERT_FILE_ENV


def use_bundled_ca_certificates() -> str | None:
    """In a frozen build, set ``SSL_CERT_FILE`` to certifi's bundle when OpenSSL has no CA file.

    Runs after the env files load, so ``SSL_CERT_FILE`` or ``SSL_CERT_DIR``
    from the shell or ``~/.opensre/.env`` (a TLS-inspecting proxy's CA) is
    kept, and so is a default CA file that exists here. Setting only the file
    leaves OpenSSL's default directory in use: a directory that is empty or
    holds hashed certificates adds to certifi rather than replacing it.
    Returns the bundle it set, or None when it set nothing.
    """
    if not getattr(sys, "frozen", False):
        return None
    if os.environ.get(SSL_CERT_FILE_ENV) or os.environ.get(SSL_CERT_DIR_ENV):
        return None
    # The path OpenSSL was compiled with is set even when that file was never
    # installed here (Homebrew's ``cert.pem`` on a Mac without it). A non-empty
    # path is not a bundle this process can load.
    cafile = ssl.get_default_verify_paths().cafile
    if cafile and os.path.isfile(cafile):
        return None
    import certifi

    bundle = certifi.where()
    os.environ[SSL_CERT_FILE_ENV] = bundle
    return bundle


__all__ = ["use_bundled_ca_certificates"]
