from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class InputRecord:
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
    customer_id: str
    order_id: str
    amount: float
    amount_cents: int
    timestamp: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
