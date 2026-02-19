from errors import DomainError
from schemas import InputRecord, ProcessedRecord


def validate_data(raw_records: list[dict], required_fields: list[str]) -> None:
    if not raw_records:
        raise DomainError("No data records found")

    for i, record in enumerate(raw_records):
        missing = [f for f in required_fields if f not in record]
        if missing:
            raise DomainError(
                f"Schema validation failed: Missing fields {missing} in record {i}"
            )


def transform_data(raw_records: list[dict]) -> list[ProcessedRecord]:
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
            raise DomainError(f"Data type error in record {i}: {e}") from e
    return processed


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    validate_data(raw_records, required_fields)
    return transform_data(raw_records)
