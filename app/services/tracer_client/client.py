"""Unified Tracer API client composed from mixins."""

from app.services.tracer_client.aws_batch_jobs import AWSBatchJobsMixin
from app.services.tracer_client.tracer_integrations import TracerIntegrationsMixin
from app.services.tracer_client.tracer_logs import TracerLogsMixin
from app.services.tracer_client.tracer_pipelines import TracerPipelinesMixin
from app.services.tracer_client.tracer_tools import TracerToolsMixin


class TracerClient(
    TracerPipelinesMixin,
    TracerToolsMixin,
    AWSBatchJobsMixin,
    TracerLogsMixin,
    TracerIntegrationsMixin,
):
    """Unified HTTP client for Tracer API (staging and web app)."""

    pass
