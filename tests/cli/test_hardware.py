"""Tests for app/cli/local_llm/hardware.py — hardware detection and model recommendation."""

from __future__ import annotations

import sys
from unittest.mock import mock_open, patch

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
# HardwareProfile
# ===========================================================================


class TestHardwareProfile:
    def test_is_frozen_dataclass(self) -> None:
        hw = HardwareProfile(
            total_ram_gb=16.0,
            available_ram_gb=8.0,
            arch="arm64",
            is_apple_silicon=True,
            has_nvidia_gpu=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            hw.total_ram_gb = 32.0  # type: ignore[misc]

    def test_fields_accessible(self) -> None:
        hw = HardwareProfile(
            total_ram_gb=32.0,
            available_ram_gb=16.0,
            arch="x86_64",
            is_apple_silicon=False,
            has_nvidia_gpu=True,
        )
        assert hw.total_ram_gb == 32.0
        assert hw.available_ram_gb == 16.0
        assert hw.arch == "x86_64"
        assert hw.is_apple_silicon is False
        assert hw.has_nvidia_gpu is True


# ===========================================================================
# _get_total_ram_gb
# ===========================================================================


class TestGetTotalRamGb:
    def test_linux_reads_proc_meminfo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        meminfo = "MemTotal:       16384000 kB\nMemFree: 8000000 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = _get_total_ram_gb()
        assert abs(result - 16384000 / (1024**2)) < 0.01

    def test_linux_returns_fallback_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("builtins.open", side_effect=OSError("no file")):
            result = _get_total_ram_gb()
        assert result == _FALLBACK_RAM_GB

    def test_darwin_reads_sysctl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("subprocess.check_output", return_value=str(16 * 1024**3)):
            result = _get_total_ram_gb()
        assert abs(result - 16.0) < 0.01

    def test_darwin_returns_fallback_on_subprocess_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("subprocess.check_output", side_effect=Exception("no sysctl")):
            result = _get_total_ram_gb()
        assert result == _FALLBACK_RAM_GB

    def test_unknown_platform_returns_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        result = _get_total_ram_gb()
        assert result == _FALLBACK_RAM_GB


# ===========================================================================
# _get_available_ram_gb
# ===========================================================================


class TestGetAvailableRamGb:
    def test_linux_reads_memavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        meminfo = "MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\n"
        with patch("builtins.open", mock_open(read_data=meminfo)):
            result = _get_available_ram_gb(16.0)
        assert abs(result - 8192000 / (1024**2)) < 0.01

    def test_linux_falls_back_to_half_total_on_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("builtins.open", side_effect=OSError("no file")):
            result = _get_available_ram_gb(16.0)
        assert result == 8.0

    def test_darwin_reads_hw_usermem(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("subprocess.check_output", return_value=str(8 * 1024**3)):
            result = _get_available_ram_gb(16.0)
        assert abs(result - 8.0) < 0.01

    def test_darwin_falls_back_to_half_total_on_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("subprocess.check_output", side_effect=Exception("fail")):
            result = _get_available_ram_gb(16.0)
        assert result == 8.0

    def test_unknown_platform_returns_half_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        result = _get_available_ram_gb(20.0)
        assert result == 10.0


# ===========================================================================
# detect_hardware
# ===========================================================================


class TestDetectHardware:
    def _patch_ram(
        self,
        monkeypatch: pytest.MonkeyPatch,
        total: float = 16.0,
        available: float = 8.0,
    ) -> None:
        monkeypatch.setattr("app.cli.local_llm.hardware._get_total_ram_gb", lambda: total)
        monkeypatch.setattr(
            "app.cli.local_llm.hardware._get_available_ram_gb", lambda _t: available
        )

    def test_returns_hardware_profile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_ram(monkeypatch)
        hw = detect_hardware()
        assert isinstance(hw, HardwareProfile)

    def test_detects_apple_silicon_on_darwin_arm64(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_ram(monkeypatch)
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("platform.machine", return_value="arm64"):
            hw = detect_hardware()
        assert hw.is_apple_silicon is True

    def test_not_apple_silicon_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_ram(monkeypatch)
        monkeypatch.setattr(sys, "platform", "linux")
        with patch("platform.machine", return_value="x86_64"):
            hw = detect_hardware()
        assert hw.is_apple_silicon is False

    def test_not_apple_silicon_on_darwin_x86(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_ram(monkeypatch)
        monkeypatch.setattr(sys, "platform", "darwin")
        with patch("platform.machine", return_value="x86_64"):
            hw = detect_hardware()
        assert hw.is_apple_silicon is False

    def test_detects_nvidia_gpu_when_nvidia_smi_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._patch_ram(monkeypatch)
        with patch("shutil.which", return_value="/usr/bin/nvidia-smi"):
            hw = detect_hardware()
        assert hw.has_nvidia_gpu is True

    def test_no_nvidia_gpu_when_smi_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_ram(monkeypatch)
        with patch("shutil.which", return_value=None):
            hw = detect_hardware()
        assert hw.has_nvidia_gpu is False

    def test_ram_values_passed_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_ram(monkeypatch, total=64.0, available=32.0)
        hw = detect_hardware()
        assert hw.total_ram_gb == 64.0
        assert hw.available_ram_gb == 32.0


# ===========================================================================
# recommend_model
# ===========================================================================


class TestRecommendModel:
    def _hw(
        self,
        *,
        total: float,
        available: float,
        apple_silicon: bool = False,
        nvidia: bool = False,
        arch: str = "x86_64",
    ) -> HardwareProfile:
        return HardwareProfile(
            total_ram_gb=total,
            available_ram_gb=available,
            arch=arch,
            is_apple_silicon=apple_silicon,
            has_nvidia_gpu=nvidia,
        )

    def test_apple_silicon_16gb_recommends_8b(self) -> None:
        hw = self._hw(total=16.0, available=14.0, apple_silicon=True, arch="arm64")
        model, reason = recommend_model(hw)
        assert model == "llama3.1:8b"
        assert "Apple Silicon" in reason

    def test_apple_silicon_8gb_falls_back_to_3b(self) -> None:
        hw = self._hw(total=8.0, available=4.0, apple_silicon=True, arch="arm64")
        model, _ = recommend_model(hw)
        assert model == "llama3.2"

    def test_nvidia_gpu_recommends_8b(self) -> None:
        hw = self._hw(total=16.0, available=6.0, nvidia=True)
        model, _ = recommend_model(hw)
        assert model == "llama3.1:8b"

    def test_high_ram_no_gpu_recommends_8b(self) -> None:
        hw = self._hw(total=32.0, available=24.0)
        model, _ = recommend_model(hw)
        assert model == "llama3.1:8b"

    def test_low_ram_no_gpu_recommends_3b(self) -> None:
        hw = self._hw(total=8.0, available=4.0)
        model, _ = recommend_model(hw)
        assert model == "llama3.2"

    def test_reason_always_returned(self) -> None:
        hw = self._hw(total=8.0, available=4.0)
        model, reason = recommend_model(hw)
        assert isinstance(reason, str)
        assert len(reason) > 0

    def test_safe_ram_capped_at_half_total(self) -> None:
        # available=16 exceeds total*0.5=8, so safe_ram is capped at 8 GB.
        # Without the cap, safe_ram would be 16 (>= 12) → llama3.1:8b.
        # With the cap, safe_ram is 8 (< 12) → llama3.2.
        hw = self._hw(total=16.0, available=16.0)
        model, reason = recommend_model(hw)
        assert model == "llama3.2"
        assert "8GB" in reason
        assert "16GB" not in reason

    def test_exactly_12gb_available_picks_8b(self) -> None:
        hw = self._hw(total=24.0, available=12.0)
        model, _ = recommend_model(hw)
        assert model == "llama3.1:8b"
