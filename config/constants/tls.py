"""Names of the environment variables OpenSSL reads for its CA certificates."""

from __future__ import annotations

#: A CA bundle file OpenSSL trusts instead of its compiled-in default file.
SSL_CERT_FILE_ENV = "SSL_CERT_FILE"
#: Directories of hashed CA certificates OpenSSL reads instead of its default one.
SSL_CERT_DIR_ENV = "SSL_CERT_DIR"

__all__ = ["SSL_CERT_DIR_ENV", "SSL_CERT_FILE_ENV"]
