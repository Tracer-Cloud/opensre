"""Feature engineering business logic for ML pipeline."""

from typing import Any

from errors import DomainError
from schemas import InputRecord, ProcessedRecord


def compute_ml_features(raw_features: dict[str, Any], event_type: str) -> dict[str, float]:
    """Compute ML feature vector from raw features.

    Feature engineering for ML models:
    - Normalize numerical features
    - Encode categorical features
    - Compute interaction features
    - Generate embedding-ready features

    Args:
        raw_features: Raw feature values from upstream
        event_type: Type of event (affects feature computation)

    Returns:
        Dictionary of computed feature values for ML consumption
    """
    features = {}

    # Extract and normalize numerical features
    features["value_normalized"] = float(raw_features.get("value", 0.0)) / 100.0
    features["duration_seconds"] = float(raw_features.get("duration", 0))
    features["count"] = float(raw_features.get("count", 1))

    # Event type encoding (one-hot style)
    event_types = ["click", "view", "purchase", "add_to_cart"]
    for et in event_types:
        features[f"event_{et}"] = 1.0 if event_type == et else 0.0

    # Interaction features (typical in ML pipelines)
    features["value_per_second"] = (
        features["value_normalized"] / max(features["duration_seconds"], 1.0)
    )
    features["avg_value_per_count"] = features["value_normalized"] / max(features["count"], 1.0)

    # Temporal features (for time-series ML)
    features["is_weekend"] = float(raw_features.get("is_weekend", 0))
    features["hour_of_day"] = float(raw_features.get("hour", 0)) / 24.0

    return features


def validate_and_transform(
    raw_records: list[dict], required_fields: list[str]
) -> list[ProcessedRecord]:
    """Validates events and engineers features for ML models.

    Args:
        raw_records: List of raw event records
        required_fields: Required fields for schema validation

    Returns:
        List of ProcessedRecord objects with computed ML features

    Raises:
        DomainError: If validation fails (missing fields, type errors)
    """
    if not raw_records:
        raise DomainError("No event records found")

    processed = []
    for i, record in enumerate(raw_records):
        # 1. Schema validation - check required fields
        missing = [f for f in required_fields if f not in record]
        if missing:
            raise DomainError(f"Schema validation failed: Missing fields {missing} in record {i}")

        # 2. Parse event and compute ML features
        try:
            event = InputRecord.from_dict(record)

            # Compute feature vector for ML model
            features = compute_ml_features(event.raw_features, event.event_type)

            # Create feature hash for versioning
            feature_hash = ProcessedRecord.compute_feature_hash(features)

            processed.append(
                ProcessedRecord(
                    event_id=event.event_id,
                    user_id=event.user_id,
                    timestamp=event.timestamp,
                    event_type=event.event_type,
                    features=features,
                    feature_hash=feature_hash,
                )
            )
        except (ValueError, KeyError, TypeError) as e:
            raise DomainError(f"Feature engineering failed for record {i}: {e}") from e

    return processed
