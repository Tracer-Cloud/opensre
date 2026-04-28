import os
from unittest.mock import MagicMock, patch

from app.integrations.catalog import classify_integrations, load_env_integrations


def test_victoria_logs_load_from_env():
    """Test that VictoriaLogs config is correctly loaded from environment variables."""
    env = {
        "VICTORIA_LOGS_URL": "http://localhost:9428",
        "VICTORIA_LOGS_TENANT_ID": "123",
    }
    with patch.dict(os.environ, env):
        integrations = load_env_integrations()
        vl_integration = next((i for i in integrations if i["service"] == "victoria_logs"), None)
        assert vl_integration is not None
        assert vl_integration["credentials"]["base_url"] == "http://localhost:9428"
        assert vl_integration["credentials"]["tenant_id"] == "123"


def test_victoria_logs_classification():
    """Test that VictoriaLogs integration is correctly classified."""
    integrations = [
        {
            "id": "env-victoria-logs",
            "service": "victoria_logs",
            "status": "active",
            "credentials": {
                "base_url": "http://localhost:9428",
                "tenant_id": "123",
            },
        }
    ]
    resolved = classify_integrations(integrations)
    assert "victoria_logs" in resolved
    config = resolved["victoria_logs"]
    assert config["base_url"] == "http://localhost:9428"
    assert config["tenant_id"] == "123"


@patch("app.services.victoria_logs.client.httpx.get")
def test_victoria_logs_verify(mock_get):
    """Test VictoriaLogs verification logic."""
    from app.integrations.verify import _verify_victoria_logs

    # Mock successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = '{"row": 1}'
    mock_get.return_value = mock_response

    config = {
        "base_url": "http://localhost:9428",
        "tenant_id": "123",
    }

    result = _verify_victoria_logs("env", config)
    assert result["status"] == "verified"

    # Mock failed response
    mock_get.side_effect = Exception("API Error")

    result = _verify_victoria_logs("env", config)
    assert result["status"] == "failed"
