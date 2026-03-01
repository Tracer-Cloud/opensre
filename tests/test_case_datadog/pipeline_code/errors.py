class PipelineError(Exception):
    pass


class ValidationError(PipelineError):
    pass


class IngestError(PipelineError):
    pass
