"""Business logic for Flink batch job."""

from errors import DomainError
from schemas import InputRecord, ProcessedRecord


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    """Validates raw dicts and transforms to ProcessedRecord models.

    Args:
        raw_records: List of raw records from input data
        required_fields: List of field names that must be present

    Returns:
        List of ProcessedRecord objects

    Raises:
        DomainError: If validation fails (missing fields, type errors)
    """
    if not raw_records:
        raise DomainError("No data records found")

    processed = []
    for i, record in enumerate(raw_records):
        # 1. Validation - check required fields
        missing = [f for f in required_fields if f not in record]
        if missing:
            raise DomainError(f"Schema validation failed: Missing fields {missing} in record {i}")

        # 2. Parsing & Transformation
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
