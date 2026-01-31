from .errors import DomainError
from .schemas import InputRecord, ProcessedRecord


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    """Validate raw dicts and transform to processed records."""
    if not raw_records:
        raise DomainError("No data records found")

    processed = []
    for i, record in enumerate(raw_records):
        missing = [field for field in required_fields if field not in record]
        if missing:
            raise DomainError(f"Schema validation failed: Missing fields {missing} in record {i}")

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
from .errors import DomainError
from .schemas import InputRecord, ProcessedRecord


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    """Validate raw dicts and transform to processed records."""
    if not raw_records:
        raise DomainError("No data records found")

    processed = []
    for i, record in enumerate(raw_records):
        missing = [field for field in required_fields if field not in record]
        if missing:
            raise DomainError(f"Schema validation failed: Missing fields {missing} in record {i}")

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
