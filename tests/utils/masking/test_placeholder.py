"""Tests for placeholder mapping."""

from app.utils.masking.detectors import DetectedIdentifier, IdentifierType
from app.utils.masking.placeholder import PlaceholderMap, _get_placeholder_template


class TestGetPlaceholderTemplate:
    """Tests for _get_placeholder_template function."""

    def test_hostname_template(self) -> None:
        """Test hostname placeholder template."""
        template = _get_placeholder_template(IdentifierType.HOSTNAME)
        assert template == "<HOSTNAME_{index}>"

    def test_account_id_template(self) -> None:
        """Test account ID placeholder template."""
        template = _get_placeholder_template(IdentifierType.ACCOUNT_ID)
        assert template == "<ACCOUNT_{index}>"

    def test_cluster_name_template(self) -> None:
        """Test cluster name placeholder template."""
        template = _get_placeholder_template(IdentifierType.CLUSTER_NAME)
        assert template == "<CLUSTER_{index}>"

    def test_service_name_template(self) -> None:
        """Test service name placeholder template."""
        template = _get_placeholder_template(IdentifierType.SERVICE_NAME)
        assert template == "<SERVICE_{index}>"

    def test_ip_address_template(self) -> None:
        """Test IP address placeholder template."""
        template = _get_placeholder_template(IdentifierType.IP_ADDRESS)
        assert template == "<IP_{index}>"

    def test_email_template(self) -> None:
        """Test email placeholder template."""
        template = _get_placeholder_template(IdentifierType.EMAIL)
        assert template == "<EMAIL_{index}>"


class TestPlaceholderMap:
    """Tests for PlaceholderMap class."""

    def test_get_or_create_placeholder_new(self) -> None:
        """Test creating new placeholder."""
        pmap = PlaceholderMap()
        identifier = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)

        placeholder = pmap.get_or_create_placeholder(identifier)
        assert placeholder == "<HOSTNAME_0>"

    def test_get_or_create_placeholder_same_value(self) -> None:
        """Test that same value returns same placeholder."""
        pmap = PlaceholderMap()
        id1 = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        id2 = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 20, 31)

        ph1 = pmap.get_or_create_placeholder(id1)
        ph2 = pmap.get_or_create_placeholder(id2)

        assert ph1 == ph2 == "<HOSTNAME_0>"

    def test_get_or_create_placeholder_different_values(self) -> None:
        """Test that different values get different placeholders."""
        pmap = PlaceholderMap()
        id1 = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        id2 = DetectedIdentifier(IdentifierType.HOSTNAME, "other.com", 0, 9)

        ph1 = pmap.get_or_create_placeholder(id1)
        ph2 = pmap.get_or_create_placeholder(id2)

        assert ph1 == "<HOSTNAME_0>"
        assert ph2 == "<HOSTNAME_1>"
        assert ph1 != ph2

    def test_get_or_create_placeholder_different_types(self) -> None:
        """Test that different types have separate counters."""
        pmap = PlaceholderMap()
        host_id = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        cluster_id = DetectedIdentifier(IdentifierType.CLUSTER_NAME, "prod-cluster", 0, 12)

        host_ph = pmap.get_or_create_placeholder(host_id)
        cluster_ph = pmap.get_or_create_placeholder(cluster_id)

        assert host_ph == "<HOSTNAME_0>"
        assert cluster_ph == "<CLUSTER_0>"

    def test_get_original_value(self) -> None:
        """Test retrieving original value from placeholder."""
        pmap = PlaceholderMap()
        identifier = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)

        placeholder = pmap.get_or_create_placeholder(identifier)
        original = pmap.get_original_value(placeholder)

        assert original == "example.com"

    def test_get_original_value_unknown(self) -> None:
        """Test retrieving unknown placeholder returns None."""
        pmap = PlaceholderMap()
        result = pmap.get_original_value("<UNKNOWN_0>")
        assert result is None

    def test_unmask_text(self) -> None:
        """Test unmasking text with placeholders."""
        pmap = PlaceholderMap()
        id1 = DetectedIdentifier(IdentifierType.CLUSTER_NAME, "prod-cluster-01", 0, 15)
        id2 = DetectedIdentifier(IdentifierType.HOSTNAME, "api.example.com", 0, 15)

        pmap.get_or_create_placeholder(id1)
        pmap.get_or_create_placeholder(id2)

        masked_text = "Error in <CLUSTER_0>: connection to <HOSTNAME_0> failed"
        unmasked = pmap.unmask_text(masked_text)

        assert unmasked == "Error in prod-cluster-01: connection to api.example.com failed"

    def test_unmask_text_multiple_same_placeholder(self) -> None:
        """Test unmasking with multiple occurrences of same placeholder."""
        pmap = PlaceholderMap()
        identifier = DetectedIdentifier(IdentifierType.CLUSTER_NAME, "prod-cluster-01", 0, 15)
        pmap.get_or_create_placeholder(identifier)

        masked_text = "Check <CLUSTER_0> logs and restart <CLUSTER_0>"
        unmasked = pmap.unmask_text(masked_text)

        assert unmasked == "Check prod-cluster-01 logs and restart prod-cluster-01"

    def test_unmask_text_partial_placeholder(self) -> None:
        """Test that partial placeholders are not replaced incorrectly."""
        pmap = PlaceholderMap()
        id1 = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        id2 = DetectedIdentifier(IdentifierType.HOSTNAME, "sub.example.com", 0, 15)

        pmap.get_or_create_placeholder(id1)
        pmap.get_or_create_placeholder(id2)

        # Both <HOSTNAME_0> and <HOSTNAME_1> exist
        # Unmask should handle order correctly
        masked_text = "<HOSTNAME_0> and <HOSTNAME_1>"
        unmasked = pmap.unmask_text(masked_text)

        assert "example.com" in unmasked
        assert "sub.example.com" in unmasked

    def test_clear(self) -> None:
        """Test clearing the placeholder map."""
        pmap = PlaceholderMap()
        identifier = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        pmap.get_or_create_placeholder(identifier)

        assert len(pmap.value_to_placeholder) == 1

        pmap.clear()

        assert len(pmap.value_to_placeholder) == 0
        assert len(pmap.placeholder_to_value) == 0
        assert len(pmap.type_counters) == 0

    def test_copy(self) -> None:
        """Test copying the placeholder map."""
        pmap = PlaceholderMap()
        identifier = DetectedIdentifier(IdentifierType.HOSTNAME, "example.com", 0, 11)
        pmap.get_or_create_placeholder(identifier)

        copy = pmap.copy()

        # Copy has same data
        assert copy.get_original_value("<HOSTNAME_0>") == "example.com"

        # But is independent
        new_id = DetectedIdentifier(IdentifierType.HOSTNAME, "other.com", 0, 9)
        copy.get_or_create_placeholder(new_id)

        assert len(pmap.value_to_placeholder) == 1
        assert len(copy.value_to_placeholder) == 2

    def test_round_trip_consistency(self) -> None:
        """Test that round-trip masking produces consistent results."""
        pmap = PlaceholderMap()

        # First occurrence
        id1 = DetectedIdentifier(IdentifierType.CLUSTER_NAME, "prod-cluster-01", 0, 15)
        ph1 = pmap.get_or_create_placeholder(id1)

        # Same value appears again
        id2 = DetectedIdentifier(IdentifierType.CLUSTER_NAME, "prod-cluster-01", 50, 65)
        ph2 = pmap.get_or_create_placeholder(id2)

        # Should get same placeholder
        assert ph1 == ph2
        assert ph1 == "<CLUSTER_0>"

        # Should unmask correctly
        assert pmap.unmask_text(ph1) == "prod-cluster-01"
