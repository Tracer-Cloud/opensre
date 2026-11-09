"""Tests for shared HTTP error extraction in notification delivery."""

from __future__ import annotations

from infrastructure.delivery.notifications.delivery_errors import extract_http_error


class TestExtractHttpError:
    def test_prefers_message_field(self) -> None:
        result = extract_http_error({"message": "Missing Permissions"}, 403, "html")
        assert result == "Missing Permissions"

    def test_falls_back_to_error_message_field(self) -> None:
        result = extract_http_error({"error_message": "Queue overflow"}, 400, "html body")
        assert result == "Queue overflow"

    def test_falls_back_to_error_field(self) -> None:
        result = extract_http_error({"error": "channel_not_found"}, 400, "html body")
        assert result == "channel_not_found"

    def test_message_takes_precedence_over_error_message(self) -> None:
        result = extract_http_error({"message": "primary", "error_message": "secondary"}, 400, "")
        assert result == "primary"

    def test_error_message_takes_precedence_over_error(self) -> None:
        result = extract_http_error({"error_message": "twilio err", "error": "generic"}, 400, "")
        assert result == "twilio err"

    def test_falls_back_to_text_when_no_error_fields(self) -> None:
        result = extract_http_error({}, 502, "<html>Bad Gateway</html>")
        assert result == "<html>Bad Gateway</html>"

    def test_falls_back_to_http_status_when_no_data(self) -> None:
        result = extract_http_error({}, 500, "")
        assert result == "HTTP 500"

    def test_truncates_text_to_500_chars(self) -> None:
        long_text = "x" * 1000
        result = extract_http_error({}, 502, long_text)
        assert len(result) == 500
