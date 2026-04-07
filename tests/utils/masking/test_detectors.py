"""Tests for identifier detectors."""

from app.utils.masking.policies import (
    DetectedIdentifier,
    IdentifierType,
    find_identifiers,
)


class TestDetectedIdentifier:
    """Tests for DetectedIdentifier dataclass."""

    def test_equality(self) -> None:
        """Test identifier equality comparison."""
        id1 = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        id2 = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        id3 = DetectedIdentifier(IdentifierType.HOSTNAME, "other.com", 0, 9)

        assert id1 == id2
        assert id1 != id3
        assert id1 != "not an identifier"

    def test_hash(self) -> None:
        """Test identifier can be used in sets/dicts."""
        id1 = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        id2 = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)

        s = {id1}
        assert id2 in s


class TestFindIdentifiers:
    """Tests for find_identifiers function."""

    def test_find_no_patterns(self) -> None:
        """Test finding with no patterns returns empty list."""
        text = "Error in prod-cluster-01"
        results = find_identifiers(text, None, None, None, None, None, None, None)
        assert results == []

    def test_find_hostnames(self) -> None:
        """Test finding hostnames in text."""
        import re

        text = "Connection to api.example.com failed"
        pattern = re.compile(
            r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])\b"
        )
        results = find_identifiers(text, pattern, None, None, None, None, None, None)

        assert len(results) == 1
        assert results[0].identifier_type == IdentifierType.HOSTNAME
        assert results[0].value == "api.example.com"
        assert results[0].start == 14
        assert results[0].end == 29

    def test_find_multiple_types(self) -> None:
        """Test finding multiple identifier types."""
        import re

        text = "Error in cluster-prod-01: connection to 192.168.1.1 failed"
        cluster_pattern = re.compile(
            r"\b(?:cluster|eks|k8s|kubernetes)[-_][a-zA-Z0-9-_]+\b", re.IGNORECASE
        )
        # Updated IP pattern that correctly matches 0-255 range including single digits
        ip_pattern = re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
        )

        results = find_identifiers(text, None, None, cluster_pattern, None, ip_pattern, None, None)

        assert len(results) == 2
        types = [r.identifier_type for r in results]
        assert IdentifierType.CLUSTER_NAME in types
        assert IdentifierType.IP_ADDRESS in types

    def test_find_custom_patterns(self) -> None:
        """Test finding custom patterns."""
        import re

        text = "Error code: 1234, User: ABC123"
        custom_pattern = re.compile(r"\b[A-Z]{3}\d+\b")
        results = find_identifiers(text, None, None, None, None, None, None, [custom_pattern])

        assert len(results) == 1
        assert results[0].identifier_type == IdentifierType.CUSTOM
        assert results[0].value == "ABC123"

    def test_results_sorted_by_position(self) -> None:
        """Test results are sorted by position."""
        import re

        text = "abc def ghi"
        pattern = re.compile(r"\b[a-z]+\b")
        results = find_identifiers(text, pattern, None, None, None, None, None, None)

        assert len(results) == 3
        assert results[0].start == 0  # abc
        assert results[1].start == 4  # def
        assert results[2].start == 8  # ghi

    def test_overlapping_matches_filtered(self) -> None:
        """Test that overlapping matches are filtered (keeps first)."""
        import re

        # Two patterns that might overlap
        text = "abc123def"
        pattern1 = re.compile(r"[a-z]+\d+")
        pattern2 = re.compile(r"\d+[a-z]+")

        # pattern1 matches "abc123", pattern2 matches "123def"
        # They overlap, so only first should be kept
        results = find_identifiers(text, pattern1, None, None, None, None, None, [pattern2])

        # The overlapping result should be filtered
        assert len(results) == 1
        assert results[0].value == "abc123"

    def test_overlapping_same_start_prefers_longer_match(self) -> None:
        """Test overlap handling keeps longer match when start positions are equal."""
        import re

        text = "eks-prod-cluster.us-east-1.example.com"
        cluster_pattern = re.compile(r"\b(?:cluster|eks)[-_][a-zA-Z0-9-_]+\b", re.IGNORECASE)
        hostname_pattern = re.compile(
            r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+"
            r"[a-zA-Z](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\b"
        )

        results = find_identifiers(
            text, hostname_pattern, None, cluster_pattern, None, None, None, None
        )

        assert len(results) == 1
        assert results[0].identifier_type == IdentifierType.HOSTNAME
        assert results[0].value == text

    def test_multiple_same_type(self) -> None:
        """Test finding multiple identifiers of the same type."""
        import re

        text = "Hosts: server1.example.com, server2.example.com"
        pattern = re.compile(
            r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])\b"
        )
        results = find_identifiers(text, pattern, None, None, None, None, None, None)

        assert len(results) == 2
        assert results[0].value == "server1.example.com"
        assert results[1].value == "server2.example.com"
