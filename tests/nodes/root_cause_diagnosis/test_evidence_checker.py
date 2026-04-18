from app.nodes.root_cause_diagnosis.evidence_checker import is_clearly_healthy


def test_is_clearly_healthy_pure_eks():
    raw_alert = {
        "state": "normal",
        "alert_source": "datadog",
        "commonLabels": {"severity": "info"},
        "commonAnnotations": {},
    }

    # Pure-EKS healthy evidence: pods running, no events, deployments available, nodes ready.
    eks_only = {
        "eks_pods": [{"name": "payments-api-x", "phase": "Running"}],
        "eks_events": [],
        "eks_deployments": [{"name": "payments-api", "ready": 3, "desired": 3}],
        "eks_node_health": [{"name": "node-1", "ready": "True"}],
    }

    assert is_clearly_healthy(raw_alert, eks_only) is True

def test_is_clearly_healthy_mixed():
    raw_alert = {
        "state": "normal",
        "alert_source": "datadog",
        "commonLabels": {"severity": "info"},
        "commonAnnotations": {},
    }

    mixed = {
        "eks_pods": [{"name": "payments-api-x", "phase": "Running"}],
        "datadog_logs": []
    }

    assert is_clearly_healthy(raw_alert, mixed) is True

def test_is_clearly_healthy_not_healthy_state():
    raw_alert = {
        "state": "alerting",
        "alert_source": "datadog",
        "commonLabels": {"severity": "info"},
        "commonAnnotations": {},
    }

    eks_only = {
        "eks_pods": [{"name": "payments-api-x", "phase": "Running"}],
    }

    assert is_clearly_healthy(raw_alert, eks_only) is False
