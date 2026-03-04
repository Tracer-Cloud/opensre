"""Data schemas for Flink feature engineering pipeline."""

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class InputRecord:
    """Raw event from upstream data source."""

    event_id: str
    user_id: str
    timestamp: str
    event_type: str
    raw_features: dict[str, Any]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InputRecord":
        return cls(
            event_id=data["event_id"],
            user_id=data["user_id"],
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            raw_features=data.get("raw_features", {}),
        )


@dataclass
class ProcessedRecord:
    """Feature-engineered record for ML model consumption."""

    event_id: str
    user_id: str
    timestamp: str
    event_type: str
    features: dict[str, float]
    feature_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def compute_feature_hash(features: dict[str, float]) -> str:
        """Compute deterministic hash of feature vector for versioning."""
        feature_str = json.dumps(features, sort_keys=True)
        return hashlib.md5(feature_str.encode(), usedforsecurity=False).hexdigest()[:8]
