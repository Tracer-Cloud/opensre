"""Direct unit tests for app/cli/local_llm/hardware.py.

Covers:
- _get_total_ram_gb(): Linux /proc/meminfo, macOS sysctl, and fallback paths
- _get_available_ram_gb(): Linux MemAvailable, macOS hw.usermem, fallback (50% of total)
- detect_hardware(): full profile construction, Apple Silicon detection, NVIDIA detection
- recommend_model(): all three recommendation branches

All tests are fully offline — no real hardware calls are made.

See: https://github.com/Tracer-Cloud/opensre/issues/905
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

from app.cli.local_llm.hardware import (
    HardwareProfile,
    _get_available_ram_gb,
    _get_total_ram_gb,
    detect_hardware,
    recommend_model,
)

_FALLBACK_RAM_GB = 8.0


# ===========================================================================
# _get_total_ram_gb
# ===========================================================================


class TestGetTotalRamGb:
    def test_linux_parses_proc_meminfo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        # 16 GiB = 16_777_216 kB
        meminfo = "MemTotal:       16777216 kB\nMemFree: 8000000 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = _get_total_ram_gb()
        assert pytest.approx(result, rel=1e-3) == 16.0

    def test_macos_parses_sysctl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        # 8 GiB in bytes
        bytes_8gb = str(8 * 1024**3)
        with patch("subprocess.check_output", return_value=bytes_8gb):
            result = _get_total_ram_gb()
        assert pytest.approx(result, rel=1e-3) == 8.0

    def test_linux_file_read_failure_returns_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("builtins.open", side_effect=OSError("no file")):
            result = _get_total_ram_gb()
        assert result == _FALLBACK_RAM_GB

    def test_macos_subprocess_failure_returns_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("subprocess.check_output", side_effect=Exception("sysctl not found")):
            result = _get_total_ram_gb()
        assert result == _FALLBACK_RAM_GB

    def test_unknown_platform_returns_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        result = _get_total_ram_gb()
        assert result == _FALLBACK_RAM_GB

    def test_linux_memtotal_not_in_file_returns_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        meminfo = "MemFree:  4000000 kB\nSwapTotal: 0 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = _get_total_ram_gb()
        assert result == _FALLBACK_RAM_GB


# ===========================================================================
# _get_available_ram_gb
# ===========================================================================


class TestGetAvailableRamGb:
    def test_linux_parses_memavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        # 8 GiB = 8_388_608 kB
        meminfo = "MemTotal: 16777216 kB\nMemAvailable: 8388608 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = _get_available_ram_gb(total_ram_gb=16.0)
        assert pytest.approx(result, rel=1e-3) == 8.0

    def test_macos_parses_hw_usermem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        bytes_6gb = str(6 * 1024**3)
        with patch("subprocess.check_output", return_value=bytes_6gb):
            result = _get_available_ram_gb(total_ram_gb=16.0)
        assert pytest.approx(result, rel=1e-3) == 6.0

    def test_failure_falls_back_to_half_total(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("builtins.open", side_effect=OSError("no file")):
            result = _get_available_ram_gb(total_ram_gb=16.0)
        assert pytest.approx(result) == 8.0

    def test_unknown_platform_falls_back_to_half_total(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        result = _get_available_ram_gb(total_ram_gb=20.0)
        assert pytest.approx(result) == 10.0


# ===========================================================================
# detect_hardware
# ===========================================================================


class TestDetectHardware:
    def test_returns_hardware_profile_instance(self) -> None:
        with (
            patch("app.cli.local_llm.hardware._get_total_ram_gb", return_value=16.0),
            patch("app.cli.local_llm.hardware._get_available_ram_gb", return_value=8.0),
            patch("platform.machine", return_value="x86_64"),
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value=None),
        ):
            hw = detect_hardware()
        assert isinstance(hw, HardwareProfile)

    def test_apple_silicon_detected_on_darwin_arm64(self) -> None:
        with (
            patch("app.cli.local_llm.hardware._get_total_ram_gb", return_value=16.0),
            patch("app.cli.local_llm.hardware._get_available_ram_gb", return_value=8.0),
            patch("platform.machine", return_value="arm64"),
            patch("sys.platform", "darwin"),
            patch("shutil.which", return_value=None),
        ):
            hw = detect_hardware()
        assert hw.is_apple_silicon is True
        assert hw.arch == "arm64"

    def test_apple_silicon_not_set_on_linux_arm64(self) -> None:
        with (
            patch("app.cli.local_llm.hardware._get_total_ram_gb", return_value=16.0),
            patch("app.cli.local_llm.hardware._get_available_ram_gb", return_value=8.0),
            patch("platform.machine", return_value="arm64"),
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value=None),
        ):
            hw = detect_hardware()
        assert hw.is_apple_silicon is False

    def test_nvidia_detected_when_nvidia_smi_present(self) -> None:
        with (
            patch("app.cli.local_llm.hardware._get_total_ram_gb", return_value=32.0),
            patch("app.cli.local_llm.hardware._get_available_ram_gb", return_value=16.0),
            patch("platform.machine", return_value="x86_64"),
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value="/usr/bin/nvidia-smi"),
        ):
            hw = detect_hardware()
        assert hw.has_nvidia_gpu is True

    def test_nvidia_not_detected_when_nvidia_smi_absent(self) -> None:
        with (
            patch("app.cli.local_llm.hardware._get_total_ram_gb", return_value=16.0),
            patch("app.cli.local_llm.hardware._get_available_ram_gb", return_value=8.0),
            patch("platform.machine", return_value="x86_64"),
            patch("sys.platform", "linux"),
            patch("shutil.which", return_value=None),
        ):
            hw = detect_hardware()
        assert hw.has_nvidia_gpu is False

    def test_profile_is_frozen(self) -> None:
        hw = HardwareProfile(
            total_ram_gb=16.0,
            available_ram_gb=8.0,
            arch="x86_64",
            is_apple_silicon=False,
            has_nvidia_gpu=False,
        )
        with pytest.raises(Exception):  # frozen dataclass raises on assignment
            hw.total_ram_gb = 32.0  # type: ignore[misc]


# ===========================================================================
# recommend_model
# ===========================================================================


class TestRecommendModel:
    def _hw(
        self,
        total: float = 16.0,
        available: float = 8.0,
        arch: str = "x86_64",
        apple_silicon: bool = False,
        nvidia: bool = False,
    ) -> HardwareProfile:
        return HardwareProfile(
            total_ram_gb=total,
            available_ram_gb=available,
            arch=arch,
            is_apple_silicon=apple_silicon,
            has_nvidia_gpu=nvidia,
        )

    def test_apple_silicon_16gb_recommends_8b(self) -> None:
        hw = self._hw(total=16.0, available=8.0, arch="arm64", apple_silicon=True)
        model, reason = recommend_model(hw)
        assert model == "llama3.1:8b"
        assert "Apple Silicon" in reason

    def test_apple_silicon_8gb_falls_through_to_safe_ram_check(self) -> None:
        # 8GB total, safe_ram = min(4, 4) = 4 → below 6GB threshold → 3B
        hw = self._hw(total=8.0, available=4.0, arch="arm64", apple_silicon=True)
        model, _ = recommend_model(hw)
        assert model == "llama3.2"

    def test_nvidia_gpu_recommends_8b(self) -> None:
        hw = self._hw(total=16.0, available=4.0, nvidia=True)
        model, reason = recommend_model(hw)
        assert model == "llama3.1:8b"

    def test_high_ram_no_gpu_recommends_8b(self) -> None:
        hw = self._hw(total=32.0, available=24.0)
        model, _ = recommend_model(hw)
        assert model == "llama3.1:8b"

    def test_low_ram_no_gpu_recommends_3b(self) -> None:
        # safe_ram = min(4, 8*0.5) = 4 — below 12GB threshold
        hw = self._hw(total=8.0, available=4.0)
        model, reason = recommend_model(hw)
        assert model == "llama3.2"
        assert "3B" in reason or "lightweight" in reason

    def test_reason_string_is_non_empty(self) -> None:
        hw = self._hw()
        _, reason = recommend_model(hw)
        assert isinstance(reason, str)
        assert len(reason) > 0
