"""Tests for placeholder validation."""

import pytest

from app.utils.masking import PlaceholderIssue, ValidationSeverity, validate_placeholders
from app.utils.masking.placeholder import PlaceholderMap
from app.utils.masking.policies import DetectedIdentifier, IdentifierType


class TestValidatePlaceholders:
    """Tests for placeholder validation."""

    def test_valid_placeholders_no_issues(self) -> None:
        """Text with valid placeholders should return no issues."""
        text = "Check logs for <CLUSTER_0> and <HOSTNAME_1>"
        issues = validate_placeholders(text)
        assert len(issues) == 0

    def test_no_placeholders_no_issues(self) -> None:
        """Text without placeholders should return no issues."""
        text = "This is normal text without any identifiers"
        issues = validate_placeholders(text)
        assert len(issues) == 0

    def test_broken_placeholder_unclosed(self) -> None:
        """Detect unclosed placeholders."""
        text = "Check logs for <CLUSTER_0"
        issues = validate_placeholders(text)

        assert len(issues) == 1
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "Unclosed" in issues[0].message

    def test_broken_placeholder_missing_type(self) -> None:
        """Detect placeholders missing type prefix."""
        text = "Check logs for <_0>"
        issues = validate_placeholders(text)

        assert len(issues) == 1
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "missing type" in issues[0].message

    def test_broken_placeholder_double_brackets(self) -> None:
        """Detect double angle brackets."""
        text = "Check logs for <<CLUSTER_0>>"
        issues = validate_placeholders(text)

        assert len(issues) == 1
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "Double angle" in issues[0].message

    def test_broken_placeholder_invalid_suffix(self) -> None:
        """Detect placeholders with invalid suffix after number."""
        text = "Check logs for <CLUSTER_0abc>"
        issues = validate_placeholders(text)

        assert len(issues) == 1
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "Invalid suffix" in issues[0].message

    def test_malformed_placeholder_generic(self) -> None:
        """Detect generic malformed placeholders."""
        text = "Check logs for <UNKNOWN_TYPE_123>"
        issues = validate_placeholders(text)

        assert len(issues) == 1
        assert issues[0].severity == ValidationSeverity.ERROR
        assert "Malformed placeholder" in issues[0].message

    def test_valid_placeholder_with_map(self) -> None:
        """Valid placeholder known to map should pass."""
        placeholder_map = PlaceholderMap()
        identifier = DetectedIdentifier(
            identifier_type=IdentifierType.CLUSTER_NAME,
            value="prod-cluster-01",
            start=0,
            end=16,
        )
        placeholder = placeholder_map.get_or_create_placeholder(identifier)

        text = f"Check logs for {placeholder}"
        issues = validate_placeholders(text, placeholder_map)
        assert len(issues) == 0

    def test_unknown_placeholder_with_map(self) -> None:
        """Valid format but unknown to map should be a warning."""
        placeholder_map = PlaceholderMap()
        # Don't populate the map

        text = "Check logs for <CLUSTER_999>"
        issues = validate_placeholders(text, placeholder_map)

        assert len(issues) == 1
        assert issues[0].severity == ValidationSeverity.WARNING
        assert "not in mapping" in issues[0].message

    def test_multiple_issues(self) -> None:
        """Detect multiple issues in the same text."""
        text = "Check <CLUSTER_0>, <_1>, and <<HOSTNAME_0>> for issues"
        issues = validate_placeholders(text)

        assert len(issues) == 2  # _1 and <<HOSTNAME_0>>
        assert all(issue.severity == ValidationSeverity.ERROR for issue in issues)

    def test_position_tracking(self) -> None:
        """Issues should track position in text."""
        text = "Error in <_0> position"
        issues = validate_placeholders(text)

        assert len(issues) == 1
        assert issues[0].position == 9  # Position of <_0>


class TestHasValidPlaceholders:
    """Tests for has_valid_placeholders convenience function."""

    def test_valid_returns_true(self) -> None:
        """Text with only valid placeholders returns True."""
        from app.utils.masking import has_valid_placeholders

        assert has_valid_placeholders("Check <CLUSTER_0>") is True

    def test_no_placeholders_returns_true(self) -> None:
        """Text with no placeholders returns True."""
        from app.utils.masking import has_valid_placeholders

        assert has_valid_placeholders("Normal text") is True

    def test_broken_returns_false(self) -> None:
        """Text with broken placeholders returns False."""
        from app.utils.masking import has_valid_placeholders

        assert has_valid_placeholders("Check <CLUSTER_0") is False


class TestGetUnknownPlaceholders:
    """Tests for get_unknown_placeholders function."""

    def test_no_unknown_returns_empty(self) -> None:
        """When all placeholders are known, return empty list."""
        from app.utils.masking import get_unknown_placeholders

        placeholder_map = PlaceholderMap()
        identifier = DetectedIdentifier(
            identifier_type=IdentifierType.CLUSTER_NAME,
            value="prod-cluster-01",
            start=0,
            end=16,
        )
        placeholder = placeholder_map.get_or_create_placeholder(identifier)

        text = f"Check {placeholder}"
        unknown = get_unknown_placeholders(text, placeholder_map)
        assert len(unknown) == 0

    def test_unknown_placeholders_returned(self) -> None:
        """Return list of unknown placeholder strings."""
        from app.utils.masking import get_unknown_placeholders

        placeholder_map = PlaceholderMap()
        text = "Check <CLUSTER_0> and <CLUSTER_999>"
        unknown = get_unknown_placeholders(text, placeholder_map)

        assert "<CLUSTER_999>" in unknown


class TestPlaceholderIssueDataclass:
    """Tests for PlaceholderIssue dataclass."""

    def test_issue_creation(self) -> None:
        """PlaceholderIssue can be created with all fields."""
        issue = PlaceholderIssue(
            placeholder="<_0>",
            severity=ValidationSeverity.ERROR,
            message="Missing type prefix",
            position=10,
        )
        assert issue.placeholder == "<_0>"
        assert issue.severity == ValidationSeverity.ERROR
        assert issue.position == 10

    def test_issue_default_position(self) -> None:
        """PlaceholderIssue defaults position to -1."""
        issue = PlaceholderIssue(
            placeholder="<_0>",
            severity=ValidationSeverity.WARNING,
            message="Some warning",
        )
        assert issue.position == -1
