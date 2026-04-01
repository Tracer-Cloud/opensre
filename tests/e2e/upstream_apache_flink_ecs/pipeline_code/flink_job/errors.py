"""Error types for Flink batch job."""


class PipelineError(Exception):
    """Base class for all pipeline errors."""

    pass


class DomainError(PipelineError):
    """Errors related to business logic or data validation."""

    pass


class SystemError(PipelineError):
    """Errors related to infrastructure or external systems."""

    pass
