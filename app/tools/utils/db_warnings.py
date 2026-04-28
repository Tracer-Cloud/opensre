"""Shared warning helper for SQL tools that default to a system database."""

from __future__ import annotations


def default_db_warning(db_name: str) -> str:
    return (
        f"WARNING: No database was specified; defaulted to '{db_name}'. "
        "Results may not reflect application data."
    )
