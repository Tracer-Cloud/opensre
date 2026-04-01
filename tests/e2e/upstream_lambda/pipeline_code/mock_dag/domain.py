from contextlib import contextmanager

from .errors import DomainError
from .schemas import InputRecord, ProcessedRecord


class _NoopSpan:
    def set_attribute(self, *_args, **_kwargs) -> None:
        return None


class _NoopTracer:
    @contextmanager
    def start_as_current_span(self, *_args, **_kwargs):
        yield _NoopSpan()


tracer = _NoopTracer()


def validate_data(raw_records: list[dict], required_fields: list[str]) -> None:
    """Validate raw records against required fields schema."""
    with tracer.start_as_current_span("validate_data") as span:
        span.set_attribute("record_count", len(raw_records))
        if not raw_records:
            raise DomainError("No data records found")

        for i, record in enumerate(raw_records):
            with tracer.start_as_current_span("validate_record") as record_span:
                record_span.set_attribute("record_index", i)
                missing = [f for f in required_fields if f not in record]
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
                            customer_id=model.customer_id,
                            order_id=model.order_id,
                            amount=model.amount,
                            amount_cents=int(model.amount * 100),
                            timestamp=model.timestamp,
                        )
                    )
                except (ValueError, KeyError) as e:
                    raise DomainError(f"Data type error in record {i}: {e}") from e

        return processed


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    """Pure business logic: validates raw dicts and transforms to ProcessedRecord models."""
    validate_data(raw_records, required_fields)
    return transform_data(raw_records)
