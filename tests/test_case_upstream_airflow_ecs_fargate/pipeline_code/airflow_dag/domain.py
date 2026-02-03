from opentelemetry import trace

from .errors import DomainError
from .schemas import InputRecord, ProcessedRecord

try:
    from tracer_telemetry import get_tracer
except ImportError:  # pragma: no cover - fallback for local tooling
    def get_tracer(name: str | None = None) -> trace.Tracer:
        return trace.get_tracer(name or __name__)

tracer = get_tracer(__name__)


def validate_data(raw_records: list[dict], required_fields: list[str]) -> None:
    """Validate raw records against required fields schema."""
    with tracer.start_as_current_span("validate_data") as span:
        span.set_attribute("record_count", len(raw_records))
        if not raw_records:
            raise DomainError("No data records found")

        for i, record in enumerate(raw_records):
            with tracer.start_as_current_span("validate_record") as record_span:
                record_span.set_attribute("record_index", i)
                missing = [field for field in required_fields if field not in record]
                if missing:
                    record_span.set_attribute("missing_fields", ",".join(missing))
                    raise DomainError(
                        f"Schema validation failed: Missing fields {missing} in record {i}"
                    )


def transform_data(raw_records: list[dict]) -> list[ProcessedRecord]:
    """Transform validated raw records into ProcessedRecord models."""
    with tracer.start_as_current_span("transform_data") as span:
        span.set_attribute("record_count", len(raw_records))
        processed = []

        for i, record in enumerate(raw_records):
            with tracer.start_as_current_span("transform_record") as record_span:
                record_span.set_attribute("record_index", i)
                try:
                    model = InputRecord.from_dict(record)
                    processed.append(
                        ProcessedRecord(
                            event_id=model.event_id,
                            user_id=model.user_id,
                            event_type=model.event_type,
                            timestamp=model.timestamp,
                            feature_count=len(model.raw_features),
                        )
                    )
                except (ValueError, KeyError, TypeError) as e:
                    raise DomainError(f"Data type error in record {i}: {e}") from e

        return processed


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    """Validate raw dicts and transform to processed records."""
    validate_data(raw_records, required_fields)
    return transform_data(raw_records)
