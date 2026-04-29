from app.nodes.plan_actions.plan_actions import _seed_action_names_for_sources


def test_seed_action_names_for_sources_includes_airflow_tools() -> None:
    available_sources = {
        "airflow": {
            "base_url": "http://localhost:8080/api/v1",
            "username": "admin",
            "password": "admin",
        }
    }

    seeded = _seed_action_names_for_sources(available_sources)

    assert "get_recent_airflow_failures" in seeded
    assert "get_airflow_dag_runs" in seeded


def test_seed_action_names_for_sources_keeps_existing_seed_sources() -> None:
    available_sources = {
        "s3_audit": {"bucket": "example-bucket", "key": "audit.json"},
        "openclaw": {"url": "http://localhost:8081"},
        "airflow": {
            "base_url": "http://localhost:8080/api/v1",
            "username": "admin",
            "password": "admin",
        },
    }

    seeded = _seed_action_names_for_sources(available_sources)

    assert "get_s3_object" in seeded
    assert "search_openclaw_conversations" in seeded
    assert "list_openclaw_tools" in seeded
    assert "get_recent_airflow_failures" in seeded
    assert "get_airflow_dag_runs" in seeded


def test_seed_action_names_for_sources_without_airflow_excludes_airflow_tools() -> None:
    available_sources = {
        "grafana": {"endpoint": "http://localhost:3000", "api_key": "test"},
    }

    seeded = _seed_action_names_for_sources(available_sources)

    assert "get_recent_airflow_failures" not in seeded
    assert "get_airflow_dag_runs" not in seeded
    assert "get_airflow_task_instances" not in seeded
