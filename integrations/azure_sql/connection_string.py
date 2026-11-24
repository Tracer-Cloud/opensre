"""Putting values into an ODBC connection string so each stays one value.

The database name comes from a tool argument. Written in unquoted, a ``;`` in
it starts a new attribute, and a second server or a TLS switch placed there
would send the stored login somewhere else.
"""

from __future__ import annotations

import re

# A host name, optionally with the ``tcp:`` prefix or a named instance.
_SERVER_NAME = re.compile(
    r"(?:tcp:)?[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?:\\[A-Za-z0-9_$#-]+)?"
)


def odbc_value(value: object) -> str:
    """Quote one value so it cannot end its attribute or add another."""
    return "{" + str(value).replace("}", "}}") + "}"


def require_server_name(server: str) -> str:
    """Return ``server`` when it is a host name; the port is appended unquoted after it."""
    if not _SERVER_NAME.fullmatch(server):
        raise ValueError("Azure SQL server must be a host name.")
    return server


__all__ = ["odbc_value", "require_server_name"]
