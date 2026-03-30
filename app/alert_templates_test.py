from __future__ import annotations

import pytest

from app.alert_templates import build_alert_template


def test_build_alert_template_generic() -> None:
    template = build_alert_template("generic")

    assert template["alert_name"] == "High error rate in payments ETL"
    assert template["pipeline_name"] == "payments_etl"
    assert template["alert_source"] == "generic"


def test_build_alert_template_datadog() -> None:
    template = build_alert_template("datadog")

    assert template["alert_source"] == "datadog"
    assert template["commonAnnotations"]["query"] == "service:payments-etl status:error"


def test_build_alert_template_grafana() -> None:
    template = build_alert_template("grafana")

    assert template["alert_source"] == "grafana"
    assert template["externalURL"] == "https://your-grafana-instance.grafana.net"


def test_build_alert_template_invalid() -> None:
    with pytest.raises(ValueError, match="Unknown alert template"):
        build_alert_template("unknown")
