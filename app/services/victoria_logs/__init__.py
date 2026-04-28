from __future__ import annotations

from app.integrations.models import VictoriaLogsIntegrationConfig
from app.services.victoria_logs.client import VictoriaLogsClient

__all__ = ["VictoriaLogsClient", "VictoriaLogsIntegrationConfig"]


def make_victoria_logs_client(*args, **kwargs) -> VictoriaLogsClient | None:
    config = VictoriaLogsIntegrationConfig(*args, **kwargs)
    if not config.base_url:
        return None
    client = VictoriaLogsClient(config)
    return client if client.is_configured else None
