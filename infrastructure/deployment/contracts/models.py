"""Data models shared by Gateway runtime for multi-tenant size profiling."""

from __future__ import annotations

from enum import StrEnum


class SizeProfile(StrEnum):
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"
