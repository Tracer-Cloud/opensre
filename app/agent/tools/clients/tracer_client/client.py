"""Unified Tracer API client composed from mixins."""

from src.agent.tools.clients.tracer_client.aws_batch_jobs import AWSBatchJobsMixin
from src.agent.tools.clients.tracer_client.tracer_logs import TracerLogsMixin
from src.agent.tools.clients.tracer_client.tracer_pipelines import TracerPipelinesMixin
from src.agent.tools.clients.tracer_client.tracer_tools import TracerToolsMixin


class TracerClient(TracerPipelinesMixin, TracerToolsMixin, AWSBatchJobsMixin, TracerLogsMixin):
    """Unified HTTP client for Tracer API (staging and web app)."""

    pass
