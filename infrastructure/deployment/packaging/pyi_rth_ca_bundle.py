"""PyInstaller runtime hook: give the frozen binary's OpenSSL CA certificates it can find.

OpenSSL looks for its default CA certificates in a directory compiled into the
library. The binary bundles a libcrypto built on another machine (the one a
dependency's wheel shipped), so on a user's machine that directory does not
exist and every client relying on the defaults, ``urllib`` among them, fails
certificate verification. httpx and requests pass certifi's bundle explicitly
and are unaffected. Before any application code runs, point OpenSSL at the
certifi bundle the binary already ships, unless the user chose their own CA
locations or the defaults resolve on this machine.
"""

from __future__ import annotations

import os
import ssl
import sys


def use_bundled_ca_certificates() -> str | None:
    """Set ``SSL_CERT_FILE`` to certifi's bundle when OpenSSL would find no CA certificates.

    Returns the bundle it set, or None when ``SSL_CERT_FILE``/``SSL_CERT_DIR``
    is already set or the default CA file or directory exists here.
    """
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return None
    defaults = ssl.get_default_verify_paths()
    if defaults.cafile or defaults.capath:
        return None
    import certifi

    bundle = certifi.where()
    os.environ["SSL_CERT_FILE"] = bundle
    return bundle


if getattr(sys, "frozen", False):
    use_bundled_ca_certificates()
