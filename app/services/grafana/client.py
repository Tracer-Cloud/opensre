"""Unified Grafana Cloud client composed from mixins."""

from app.services.grafana.base import GrafanaClientBase
from app.services.grafana.loki import LokiMixin
from app.services.grafana.mimir import MimirMixin
from app.services.grafana.tempo import TempoMixin


class GrafanaClient(LokiMixin, TempoMixin, MimirMixin, GrafanaClientBase):
    """Unified client for querying Grafana Cloud Loki, Tempo, and Mimir."""

    pass
