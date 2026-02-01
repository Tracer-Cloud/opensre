class PipelineError(Exception):
    """Base class for pipeline errors."""


class DomainError(PipelineError):
    """Errors related to business logic or data validation."""


class SystemError(PipelineError):
    """Errors related to infrastructure or external systems."""
