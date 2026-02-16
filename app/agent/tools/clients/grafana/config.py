"""Grafana account configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GrafanaAccountConfig:
    """Configuration for a Grafana Cloud account."""

    account_id: str
    instance_url: str
    read_token: str
    loki_datasource_uid: str = ""
    tempo_datasource_uid: str = ""
    mimir_datasource_uid: str = ""
    description: str = ""

    @property
    def is_configured(self) -> bool:
        """Check if account has valid configuration."""
        return bool(self.instance_url and self.read_token)
