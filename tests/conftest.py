"""Shared test fixtures for SAMDUO Battery tests.

The canned telemetry is the scaled form of a real 22600 reading captured from
the Nex E6000 review unit on 2026-08-09 (battery charging at ~800 W, 69.5 %).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

MOCK_SERIAL = "E01PYA0000001"

MOCK_DEVICE_DATA = {
    "inverter_state": 0,
    "inverter_version": 111,
    "inverter_code": 1,
    "error_code": 0,
    "battery_power": -799,
    "backup_power": 0,
    "energy_charged": 7.959,
    "energy_discharged": 5.121,
    "grid_frequency": 49.9,
    "grid_voltage": 230.9,
    "offgrid_voltage": 1.5,
    "backup_enabled": 0,
    "temperature_1": 43.0,
    "temperature_2": 43.9,
    "bms_version": 106,
    "battery_soc": 69.5,
    "battery_soh": 99.9,
    "battery_voltage": 20.2,
    "ems_version": 115,
}

MOCK_POWER_CONFIG = {
    "control_power": 0,
    "control_timeout": 0,
    "control_time_left": 0,
}

MOCK_USER_INPUT = {
    "host": "192.168.1.95",
    "port": 3335,
    "name": "Test Battery",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading custom_components in all tests."""
    yield


@pytest.fixture(autouse=True)
def fast_writes(monkeypatch):
    """Zero the write-path delays so control tests do not sleep."""
    from custom_components.samduo_battery.coordinator import SamduoBatteryCoordinator

    monkeypatch.setattr(SamduoBatteryCoordinator, "_WRITE_VERIFY_DELAY_SECONDS", 0)
    monkeypatch.setattr(SamduoBatteryCoordinator, "_WRITE_RETRY_DELAY_SECONDS", 0)


@pytest.fixture
def mock_client():
    """Mock SamduoTcpClient in both the setup path and the config flow."""
    with patch("custom_components.samduo_battery.SamduoTcpClient", autospec=True) as mock_cls:
        client = mock_cls.return_value
        client.host = "192.168.1.95"
        client.port = 3335
        client.device_serial = MOCK_SERIAL
        client.consecutive_failures = 0
        client.async_connect = AsyncMock()
        client.async_disconnect = AsyncMock()
        client.get_device_data = AsyncMock(return_value=dict(MOCK_DEVICE_DATA))
        client.get_power_config = AsyncMock(return_value=dict(MOCK_POWER_CONFIG))
        client.get_backup_state = AsyncMock(return_value={"backup_enabled": 0})
        client.request = AsyncMock(return_value={"inv_backup": 0})
        # 22045 echoes the NEW values; 22013 echoes the PREVIOUS value.
        client.set_power_control = AsyncMock(side_effect=lambda p, t: {"power": p, "timeoutS": t})
        client.set_backup = AsyncMock(return_value={"inv_backup": 0})
        with patch("custom_components.samduo_battery.config_flow.SamduoTcpClient", new=mock_cls):
            yield client
