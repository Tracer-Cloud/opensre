from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class InputRecord:
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
            raw_features=data["raw_features"],
        )


@dataclass
class ProcessedRecord:
    event_id: str
    user_id: str
    event_type: str
    timestamp: str
    feature_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class InputRecord:
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
            raw_features=data["raw_features"],
        )


@dataclass
class ProcessedRecord:
    event_id: str
    user_id: str
    event_type: str
    timestamp: str
    feature_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
