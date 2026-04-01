"""Unified Grafana Cloud client composed from mixins."""

from app.tools.clients.grafana.base import GrafanaClientBase
from app.tools.clients.grafana.loki import LokiMixin
from app.tools.clients.grafana.mimir import MimirMixin
from app.tools.clients.grafana.tempo import TempoMixin


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass
