"""Grafana account configuration management.

Loads Grafana Cloud account configurations from YAML config files.
Tokens are never stored in config files - they're loaded from:
- Environment variables (development/CI)
- AWS Secrets Manager (production)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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


class GrafanaConfigLoader:
    """Loads and manages Grafana account configurations."""

    _instance: GrafanaConfigLoader | None = None
    _config: dict[str, Any] | None = None

    def __new__(cls) -> GrafanaConfigLoader:
        """Singleton pattern for config loader."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the config loader."""
        if self._config is None:
            self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        config_path = self._find_config_file()
        if config_path and config_path.exists():
            with open(config_path) as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {"accounts": {}, "default_account": "tracerbio"}

    def _find_config_file(self) -> Path | None:
        """Find the grafana accounts config file."""
        search_paths = [
            Path(__file__).parent.parent.parent.parent.parent.parent
            / "configs"
            / "grafana_accounts.yaml",
            Path.cwd() / "configs" / "grafana_accounts.yaml",
            Path.home() / ".tracer" / "grafana_accounts.yaml",
        ]

        for path in search_paths:
            if path.exists():
                return path
        return None

    def _resolve_token(self, account_config: dict[str, Any]) -> str:
        """Resolve the read token from environment or secrets manager.

        Args:
            account_config: Account configuration dict from YAML

        Returns:
            The resolved token string, or empty string if not found
        """
        token_env_var = account_config.get("read_token_env", "")
        if token_env_var:
            token = os.getenv(token_env_var, "")
            if token:
                return token

        # Future: Add AWS Secrets Manager lookup here
        # secret_name = account_config.get("read_token_secret")
        # if secret_name:
        #     return fetch_from_secrets_manager(secret_name)

        return ""

    def get_account(self, account_id: str | None = None) -> GrafanaAccountConfig:
        """Get configuration for a specific Grafana account.

        Args:
            account_id: Account identifier (e.g., "tracerbio", "customer1")
                       If None, uses the default account.

        Returns:
            GrafanaAccountConfig for the requested account
        """
        if self._config is None:
            self._load_config()

        config = self._config or {}
        effective_account_id = account_id or config.get("default_account", "tracerbio")
        accounts: dict[str, Any] = config.get("accounts", {})

        if effective_account_id not in accounts:
            return GrafanaAccountConfig(
                account_id=effective_account_id,
                instance_url="",
                read_token="",
                loki_datasource_uid="grafanacloud-logs",
                tempo_datasource_uid="grafanacloud-traces",
                mimir_datasource_uid="grafanacloud-prom",
                description=f"Account {effective_account_id} not found in config",
            )

        account_data = accounts[effective_account_id]
        datasources = account_data.get("datasources", {})

        return GrafanaAccountConfig(
            account_id=effective_account_id,
            instance_url=account_data.get("instance_url", ""),
            read_token=self._resolve_token(account_data),
            loki_datasource_uid=datasources.get("loki", "grafanacloud-logs"),
            tempo_datasource_uid=datasources.get("tempo", "grafanacloud-traces"),
            mimir_datasource_uid=datasources.get("mimir", "grafanacloud-prom"),
            description=account_data.get("description", ""),
        )

    def list_accounts(self) -> list[str]:
        """List all configured account IDs."""
        if self._config is None:
            self._load_config()
        config = self._config or {}
        accounts: dict[str, Any] = config.get("accounts", {})
        return list(accounts.keys())

    def get_default_account_id(self) -> str:
        """Get the default account ID."""
        if self._config is None:
            self._load_config()
        config = self._config or {}
        default: str = config.get("default_account", "tracerbio")
        return default

    @classmethod
    def reset(cls) -> None:
        """Reset the singleton instance (useful for testing)."""
        cls._instance = None
        cls._config = None


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
