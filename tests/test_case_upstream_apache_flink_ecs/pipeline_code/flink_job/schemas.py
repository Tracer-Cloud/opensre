"""Data schemas for Flink batch job."""

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class InputRecord:
    """Input record from external API."""

    customer_id: str
    order_id: str
    amount: float
    timestamp: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "InputRecord":
        return cls(
            customer_id=data["customer_id"],
            order_id=data["order_id"],
            amount=float(data["amount"]),
            timestamp=data["timestamp"],
        )


@dataclass
class ProcessedRecord:
    """Processed record after validation and transformation."""

    customer_id: str
    order_id: str
    amount: float
    amount_cents: int
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
