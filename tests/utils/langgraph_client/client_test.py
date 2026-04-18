"""Tests for langgraph_client.py."""

from unittest import TestCase
from unittest import main as unittest_main
from unittest.mock import MagicMock, patch

import requests

from tests.utils.conftest import LANGGRAPH_REMOTE_ENDPOINT
from tests.utils.langgraph_client import fire_alert_to_remote_langgraph_client


class TestLangGraphClient(TestCase):
    @patch("requests.post")
    def test_fire_alert_to_remote_langgraph_client_success(self, mock_post):
        """Test successful alert firing to remote endpoint."""
        # Setup mock response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        # Test data
        alert_name = "Test Alert"
        pipeline_name = "test_pipeline"
        severity = "critical"
        raw_alert = {"error": "test error"}

        # Call function
        response = fire_alert_to_remote_langgraph_client(
            alert_name=alert_name,
            pipeline_name=pipeline_name,
            severity=severity,
            raw_alert=raw_alert,
        )

        # Verify post call
        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], LANGGRAPH_REMOTE_ENDPOINT)
        self.assertEqual(kwargs["json"]["input"]["alert_name"], alert_name)
        self.assertEqual(kwargs["json"]["input"]["pipeline_name"], pipeline_name)
        self.assertEqual(kwargs["json"]["input"]["severity"], severity)
        self.assertEqual(kwargs["json"]["input"]["raw_alert"], raw_alert)
        self.assertIs(kwargs["stream"], True)
        # Verify that HTTP errors are checked
        mock_response.raise_for_status.assert_called_once()

        self.assertEqual(response, mock_response)

    @patch("requests.post")
    def test_fire_alert_to_remote_langgraph_client_failure(self, mock_post):
        """Test alert firing failure to remote endpoint."""
        # Setup mock response to raise HTTPError
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "404 Client Error"
        )
        mock_post.return_value = mock_response

        with self.assertRaises(requests.exceptions.HTTPError):
            fire_alert_to_remote_langgraph_client(
                alert_name="Fail Alert",
                pipeline_name="fail_pipeline",
                severity="warning",
                raw_alert={},
            )


if __name__ == "__main__":
    unittest_main()
