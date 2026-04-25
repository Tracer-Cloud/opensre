import pytest
import os
from app.services.prefect.client import PrefectClient

def test_prefect_client_env_compatibility():
    """
    Validates that the client correctly adapts to K8s environment variables
    vs. local Ubuntu configuration.
    """
    # Simulate K8s Service Discovery Environment Variable
    os.environ["PREFECT_API_URL"] = "http://prefect-server.prefect.svc.cluster.local:4200/api"
    
    client = PrefectClient()
    
    # Assert that the client logic correctly prioritizes the K8s service URL
    assert "cluster.local" in client.api_url
    assert client.timeout == 30  # Standard K8s internal timeout

def test_prefect_client_connectivity_logic():
    """
    Ensures the client handles the specific DNS resolution patterns 
    found in Kubernetes environments.
    """
    from app.services.prefect.client import PrefectClient
    
    # Verify that the client can be instantiated without local Ubuntu-specific paths
    client = PrefectClient(use_local_socket=False)
    assert client.transport_layer == "http/2" # Standard for cloud-native K8s ingress
