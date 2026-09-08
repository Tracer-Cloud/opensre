"""Shared database connection primitives."""

from infrastructure.database.sqlite import (
    connection as sqlite_connection,
)
from infrastructure.database.sqlite import (
    transaction as sqlite_transaction,
)

__all__ = ["sqlite_connection", "sqlite_transaction"]
