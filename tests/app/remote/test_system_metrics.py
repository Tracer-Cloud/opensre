from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.remote.system_metrics import (
    _collect_cpu,
    _collect_disk,
    _collect_process,
    _collect_uptime,
    _humanize_seconds,
    collect_system_metrics,
)


class TestCollectSystemMetrics:
    def test_returns_dict_with_expected_keys(self) -> None:
        result = collect_system_metrics()
        assert isinstance(result, dict)
        assert "platform" in result

    def test_platform_always_present(self) -> None:
        result = collect_system_metrics()
        plat = result["platform"]
        assert "os" in plat
        assert "arch" in plat
        assert "python" in plat
        assert "hostname" in plat

    def test_cpu_section_shape(self) -> None:
        cpu = _collect_cpu()
        if cpu is None:
            return
        assert isinstance(cpu["load_avg_1m"], float)
        assert isinstance(cpu["load_avg_5m"], float)
        assert isinstance(cpu["load_avg_15m"], float)
        assert isinstance(cpu["core_count"], int)
        assert cpu["core_count"] >= 1

    def test_disk_section_shape(self) -> None:
        disk = _collect_disk()
        assert disk is not None
        assert 0 <= disk["percent"] <= 100
        assert disk["total_gb"] > 0
        assert disk["used_gb"] >= 0
        assert disk["free_gb"] >= 0

    def test_cpu_returns_none_when_unavailable(self) -> None:
        def _raise_loadavg() -> tuple[float, float, float]:
            raise OSError

        fake_os = SimpleNamespace(getloadavg=_raise_loadavg, cpu_count=lambda: 1)
        with patch("app.remote.system_metrics.os", fake_os):
            assert _collect_cpu() is None

    def test_disk_returns_none_when_unavailable(self) -> None:
        with patch("shutil.disk_usage", side_effect=OSError):
            assert _collect_disk() is None

    def test_uptime_returns_none_on_unsupported_platform(self) -> None:
        with patch("sys.platform", "win32"):
            assert _collect_uptime() is None

    def test_process_returns_none_on_failure(self) -> None:
        def _raise_getrusage(_rusage_self: object) -> None:
            raise Exception("no")

        fake_resource = SimpleNamespace(getrusage=_raise_getrusage, RUSAGE_SELF=object())
        with patch("app.remote.system_metrics._resource", fake_resource):
            assert _collect_process() is None


class TestHumanizeSeconds:
    def test_minutes_only(self) -> None:
        assert _humanize_seconds(300) == "5m"

    def test_hours_and_minutes(self) -> None:
        assert _humanize_seconds(3660) == "1h 1m"

    def test_days_hours_minutes(self) -> None:
        assert _humanize_seconds(90060) == "1d 1h 1m"

    def test_zero_seconds(self) -> None:
        assert _humanize_seconds(0) == "0m"

    def test_exact_day(self) -> None:
        assert _humanize_seconds(86400) == "1d"

    def test_days_and_hours(self) -> None:
        assert _humanize_seconds(302400) == "3d 12h"
