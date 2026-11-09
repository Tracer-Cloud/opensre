from typing import Any

from integrations.splunk.tools import _map_query_splunk_logs


def test_query_splunk_logs_records_evidence_entry() -> None:
    # Arrange
    evidence: dict[str, Any] = {}
    output = {
        "logs": [
            {"message": "connection failed"},
            {"message": "request timeout"},
        ]
    }

    # Act
    _map_query_splunk_logs(evidence, output, {})

    # Assert
    entries = evidence["catalog_entries"]
    assert isinstance(entries, list)
    assert any(
        entry["source"] == "query_splunk_logs"
        and entry["label"] == "Splunk Logs"
        and entry["summary"] == "2 logs"
        for entry in entries
    )
