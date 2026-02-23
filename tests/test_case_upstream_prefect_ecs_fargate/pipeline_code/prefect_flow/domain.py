"""
Domain logic for data validation and transformation.

This module contains pure business logic instrumented with OpenTelemetry spans.
Instrumentation follows these principles:
- Spans wrap meaningful business operations, not individual records
- Exceptions are recorded on spans with proper error status
- Context propagation is automatic via OpenTelemetry
- Attribute names follow semantic conventions
"""

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from .errors import DomainError
from .schemas import InputRecord, ProcessedRecord

tracer = trace.get_tracer(__name__)


def validate_data(raw_records: list[dict], required_fields: list[str]) -> None:
    """
    Validate raw records against required fields schema.

    Raises:
        DomainError: If records are empty or missing required fields.
    """
    with tracer.start_as_current_span("domain.validate_data") as span:
        span.set_attribute("data.record.count", len(raw_records))
        span.set_attribute("data.validation.required_fields", ",".join(required_fields))

        try:
            if not raw_records:
                raise DomainError("No data records found")

            for i, record in enumerate(raw_records):
                missing = [f for f in required_fields if f not in record]
                if missing:
                    span.set_attribute("data.validation.failed_record_index", i)
                    span.set_attribute("data.validation.missing_fields", ",".join(missing))
                    raise DomainError(
                        f"Schema validation failed: Missing fields {missing} in record {i}"
                    )

            span.set_attribute("data.validation.status", "success")

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def transform_data(raw_records: list[dict]) -> list[ProcessedRecord]:
    """
    Transform validated raw records into ProcessedRecord models.

    Raises:
        DomainError: If data type conversion fails.
    """
    with tracer.start_as_current_span("domain.transform_data") as span:
        span.set_attribute("data.record.count", len(raw_records))

        try:
            processed = []
            for i, record in enumerate(raw_records):
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
                    span.set_attribute("data.transform.failed_record_index", i)
                    raise DomainError(f"Data type error in record {i}: {e}") from e

            span.set_attribute("data.transform.output_count", len(processed))
            span.set_attribute("data.transform.status", "success")
            return processed

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    """
    Pure business logic: validates raw dicts and transforms to ProcessedRecord models.

    This is the main entry point for domain operations. Validation and transformation
    are wrapped in their own spans for granular observability.
    """
    validate_data(raw_records, required_fields)
    return transform_data(raw_records)
