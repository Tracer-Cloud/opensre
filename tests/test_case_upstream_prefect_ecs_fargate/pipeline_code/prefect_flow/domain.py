from .errors import DomainError
from .schemas import InputRecord, ProcessedRecord


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    """Pure business logic: validates raw dicts and transforms to ProcessedRecord models."""
    if not raw_records:
        raise DomainError("No data records found")

    processed = []
    for i, record in enumerate(raw_records):
        # 1. Validation
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
