"""Sensitive identifier detectors.

Detects and classifies sensitive infrastructure identifiers in text.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from re import Pattern


class IdentifierType(Enum):
    """Types of sensitive identifiers that can be detected."""

    HOSTNAME = auto()
    ACCOUNT_ID = auto()
    CLUSTER_NAME = auto()
    SERVICE_NAME = auto()
    IP_ADDRESS = auto()
    EMAIL = auto()
    CUSTOM = auto()


@dataclass(frozen=True)
class DetectedIdentifier:
    """A detected sensitive identifier in text.

    Attributes:
        identifier_type: The type of identifier detected
        value: The actual value found in the text
        start: Start position in the original text
        end: End position in the original text
    """

    identifier_type: IdentifierType
    value: str
    start: int
    end: int

    def __hash__(self) -> int:
        return hash((self.identifier_type, self.value, self.start, self.end))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DetectedIdentifier):
            return NotImplemented
        return (
            self.identifier_type == other.identifier_type
            and self.value == other.value
            and self.start == other.start
            and self.end == other.end
        )


def find_identifiers(
    text: str,
    hostname_pattern: Pattern[str] | None,
    account_id_pattern: Pattern[str] | None,
    cluster_name_pattern: Pattern[str] | None,
    service_name_pattern: Pattern[str] | None,
    ip_address_pattern: Pattern[str] | None,
    email_pattern: Pattern[str] | None,
    custom_patterns: list[Pattern[str]] | None = None,
) -> list[DetectedIdentifier]:
    """Find all sensitive identifiers in text.

    Args:
        text: The text to search
        hostname_pattern: Regex for hostnames
        account_id_pattern: Regex for account IDs
        cluster_name_pattern: Regex for cluster names
        service_name_pattern: Regex for service names
        ip_address_pattern: Regex for IP addresses
        email_pattern: Regex for email addresses
        custom_patterns: Additional custom regex patterns

    Returns:
        List of detected identifiers sorted by position
    """
    results: list[DetectedIdentifier] = []

    pattern_map: dict[Pattern[str] | None, IdentifierType] = {
        hostname_pattern: IdentifierType.HOSTNAME,
        account_id_pattern: IdentifierType.ACCOUNT_ID,
        cluster_name_pattern: IdentifierType.CLUSTER_NAME,
        service_name_pattern: IdentifierType.SERVICE_NAME,
        ip_address_pattern: IdentifierType.IP_ADDRESS,
        email_pattern: IdentifierType.EMAIL,
    }

    for pattern, id_type in pattern_map.items():
        if pattern is None:
            continue
        for match in pattern.finditer(text):
            results.append(
                DetectedIdentifier(
                    identifier_type=id_type,
                    value=match.group(0),
                    start=match.start(),
                    end=match.end(),
                )
            )

    if custom_patterns:
        for pattern in custom_patterns:
            for match in pattern.finditer(text):
                results.append(
                    DetectedIdentifier(
                        identifier_type=IdentifierType.CUSTOM,
                        value=match.group(0),
                        start=match.start(),
                        end=match.end(),
                    )
                )

    # Sort by position and remove overlapping matches (keep first occurrence)
    results.sort(key=lambda x: (x.start, x.end))
    filtered: list[DetectedIdentifier] = []
    last_end = -1
    for r in results:
        if r.start >= last_end:
            filtered.append(r)
            last_end = r.end

    return filtered
