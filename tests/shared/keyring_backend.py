"""Test-only keyring backend for subprocess-based smoke tests."""

from __future__ import annotations

from keyring.backend import KeyringBackend  # type: ignore[import-untyped]


class MemoryKeyring(KeyringBackend):
    priority = 1.0

    def __init__(self) -> None:
        super().__init__()
        self._entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self._entries.pop((service, username), None)
