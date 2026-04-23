"""Tests for s3_upload_validate to ensure functions are pure."""

import copy
import json
from unittest.mock import MagicMock

from tests.utils.s3_upload_validate import (
    INVALID_PAYLOAD,
    VALID_PAYLOAD,
    upload_test_data,
    verify_output,
)


class TestPayloadConstants:
    """Test fixed payloads."""

    def test_valid_payload_contains_customer_id(self):
        """Valid payload has customer_id in all records."""
        for record in VALID_PAYLOAD["data"]:
            assert "customer_id" in record

    def test_invalid_payload_missing_customer_id(self):
        """Invalid payload lacks customer_id."""
        for record in INVALID_PAYLOAD["data"]:
            assert "customer_id" not in record


class TestUploadTestData:
    """Test upload_test_data is pure (uses injected dependencies)."""

    def test_uses_injected_s3_client(self):
        """Function uses provided S3 client, not global state."""
        mock_s3 = MagicMock()
        upload_test_data("test-bucket", VALID_PAYLOAD, s3_client=mock_s3)

        mock_s3.put_object.assert_called_once()
        call_args = mock_s3.put_object.call_args
        assert call_args.kwargs["Bucket"] == "test-bucket"

    def test_uses_payload_dict(self):
        """Function uses provided payload dict."""
        mock_s3 = MagicMock()
        custom_payload = {"custom": True, "items": [1, 2, 3]}

        upload_test_data("bucket", custom_payload, s3_client=mock_s3)

        body = mock_s3.put_object.call_args.kwargs["Body"]
        data = json.loads(body)
        assert data["custom"] is True

    def test_does_not_mutate_payload(self):
        """Function does not modify the payload."""
        mock_s3 = MagicMock()
        payload = copy.deepcopy(VALID_PAYLOAD)
        original = copy.deepcopy(payload)

        upload_test_data("bucket", payload, s3_client=mock_s3)

        assert payload == original

    def test_returns_test_data_with_key(self):
        """Returns TestData with key and correlation_id."""
        mock_s3 = MagicMock()
        result = upload_test_data("bucket", VALID_PAYLOAD, s3_client=mock_s3)

        assert result.key.startswith("ingested/")
        assert result.correlation_id.startswith("local-test-")


class TestVerifyOutput:
    """Test verify_output is pure (uses injected dependencies)."""

    def test_uses_injected_s3_client(self):
        """Function uses provided S3 client, not global state."""
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"data": []}')}

        verify_output("test-bucket", "ingested/key", s3_client=mock_s3)

        mock_s3.get_object.assert_called_once()
        assert mock_s3.get_object.call_args.kwargs["Bucket"] == "test-bucket"

    def test_transforms_key_correctly(self):
        """Replaces ingested/ with processed/ in key."""
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"data": []}')}

        verify_output("bucket", "ingested/2026/data.json", s3_client=mock_s3)

        expected_key = "processed/2026/data.json"
        assert mock_s3.get_object.call_args.kwargs["Key"] == expected_key

    def test_returns_true_on_success(self):
        """Returns True when object exists."""
        mock_s3 = MagicMock()
        mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: b'{"data": [{"id": 1}]}')}

        assert verify_output("bucket", "ingested/key", s3_client=mock_s3) is True
