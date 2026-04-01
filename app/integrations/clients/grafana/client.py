"""Unified Grafana Cloud client composed from mixins."""

from app.integrations.clients.grafana.base import GrafanaClientBase
from app.integrations.clients.grafana.loki import LokiMixin
from app.integrations.clients.grafana.mimir import MimirMixin
from app.integrations.clients.grafana.tempo import TempoMixin


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass
