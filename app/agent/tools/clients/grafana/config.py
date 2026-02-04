"""Grafana account configuration management.

Loads Grafana Cloud account configurations from environment variables.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.grafana_config import (
    get_account_datasource_uids,
    get_account_instance_url,
    get_account_read_token,
    list_account_ids,
    load_env,
)


@dataclass(frozen=True)
class GrafanaAccountConfig:
    """Configuration for a Grafana Cloud account."""

    account_id: str
    instance_url: str
    read_token: str
    loki_datasource_uid: str
    tempo_datasource_uid: str
    mimir_datasource_uid: str
    description: str = ""

    @property
    def is_configured(self) -> bool:
        """Check if account has valid configuration."""
        return bool(self.instance_url and self.read_token)


DEFAULT_ACCOUNT_ID = "tracerbio"
DEFAULT_INSTANCE_URL = "https://tracerbio.grafana.net"
DEFAULT_LOKI_UID = "grafanacloud-logs"
DEFAULT_TEMPO_UID = "grafanacloud-traces"
DEFAULT_MIMIR_UID = "grafanacloud-prom"


def _build_account_config(account_id: str) -> GrafanaAccountConfig:
    token = get_account_read_token(account_id)
    instance_url = get_account_instance_url(account_id)
    loki_uid, tempo_uid, mimir_uid = get_account_datasource_uids(account_id)

    description = f"Account {account_id} from environment"

    return GrafanaAccountConfig(
        account_id=account_id,
        instance_url=instance_url,
        read_token=token,
        loki_datasource_uid=loki_uid,
        tempo_datasource_uid=tempo_uid,
        mimir_datasource_uid=mimir_uid,
        description=description,
    )


def _discover_accounts() -> dict[str, GrafanaAccountConfig]:
    accounts: dict[str, GrafanaAccountConfig] = {}
    accounts[DEFAULT_ACCOUNT_ID] = _build_account_config(DEFAULT_ACCOUNT_ID)

    for account_id in list_account_ids():
        if account_id not in accounts:
            accounts[account_id] = _build_account_config(account_id)

    return accounts


class GrafanaConfigLoader:
    """Loads and manages Grafana account configurations."""

    _instance: GrafanaConfigLoader | None = None
    _accounts: dict[str, GrafanaAccountConfig] | None = None

    def __new__(cls) -> GrafanaConfigLoader:
        """Singleton pattern for config loader."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the config loader."""
        if self._accounts is None:
            load_env()
            self._accounts = _discover_accounts()

    def get_account(self, account_id: str | None = None) -> GrafanaAccountConfig:
        """Get configuration for a specific Grafana account."""
        if self._accounts is None:
            self._accounts = _discover_accounts()

        effective_account_id = account_id or DEFAULT_ACCOUNT_ID
        account = self._accounts.get(effective_account_id)
        if account:
            return account

        return GrafanaAccountConfig(
            account_id=effective_account_id,
            instance_url="",
            read_token="",
            loki_datasource_uid=DEFAULT_LOKI_UID,
            tempo_datasource_uid=DEFAULT_TEMPO_UID,
            mimir_datasource_uid=DEFAULT_MIMIR_UID,
            description=f"Account {effective_account_id} not configured",
        )

    def list_accounts(self) -> list[str]:
        """List all configured account IDs."""
        if self._accounts is None:
            self._accounts = _discover_accounts()
        return list(self._accounts.keys())

    def get_default_account_id(self) -> str:
        """Get the default account ID."""
        return DEFAULT_ACCOUNT_ID

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None
        cls._accounts = None


def get_grafana_config(account_id: str | None = None) -> GrafanaAccountConfig:
    """Get Grafana configuration for an account.

    Args:
        account_id: Account identifier. If None, uses default account.

    Returns:
        GrafanaAccountConfig for the requested account
    """
    loader = GrafanaConfigLoader()
    return loader.get_account(account_id)


def list_grafana_accounts() -> list[str]:
    """List all configured Grafana account IDs."""
    loader = GrafanaConfigLoader()
    return loader.list_accounts()
