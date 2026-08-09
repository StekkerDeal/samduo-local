"""Tests for the coordinator: merge, secondary-poll tolerance, failure tolerance."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.samduo_battery.coordinator import SamduoBatteryCoordinator

from .conftest import MOCK_DEVICE_DATA, MOCK_SERIAL


def _make_coordinator(hass: HomeAssistant, mock_client) -> SamduoBatteryCoordinator:
    return SamduoBatteryCoordinator(
        hass,
        mock_client,
        "Test Battery",
        serial=MOCK_SERIAL,
        model="Nex E6000",
        poll_interval=5,
    )


async def test_update_merges_device_data_and_power_config(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data["battery_soc"] == 69.5
    assert coordinator.data["control_time_left"] == 0
    # 22023 is never polled: 22600 already carries the backup state.
    mock_client.get_backup_state.assert_not_awaited()


async def test_failed_power_config_keeps_last_control_values(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await coordinator.async_refresh()

    mock_client.get_power_config.return_value = None
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.data["control_time_left"] == 0
    assert coordinator.data["battery_soc"] == 69.5


async def test_failure_tolerance_serves_last_good_then_fails(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await coordinator.async_refresh()
    assert coordinator.last_update_success

    mock_client.get_device_data.return_value = None
    for _ in range(5):
        await coordinator.async_refresh()
        assert coordinator.last_update_success
        assert coordinator.data["battery_soc"] == 69.5

    await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_first_poll_failure_fails_immediately(hass: HomeAssistant, mock_client) -> None:
    # Without a last good frame there is nothing to serve - fail so setup retries.
    mock_client.get_device_data.return_value = None
    coordinator = _make_coordinator(hass, mock_client)

    await coordinator.async_refresh()
    assert not coordinator.last_update_success


async def test_recovery_resets_failure_streak(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await coordinator.async_refresh()

    mock_client.get_device_data.return_value = None
    for _ in range(4):
        await coordinator.async_refresh()

    mock_client.get_device_data.return_value = dict(MOCK_DEVICE_DATA)
    await coordinator.async_refresh()
    assert coordinator.last_update_success

    # A fresh outage gets the full tolerance again.
    mock_client.get_device_data.return_value = None
    for _ in range(5):
        await coordinator.async_refresh()
        assert coordinator.last_update_success


async def test_device_info_composes_firmware_versions(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await coordinator.async_refresh()

    info = coordinator.device_info
    assert info["identifiers"] == {("samduo_battery", MOCK_SERIAL)}
    assert info["manufacturer"] == "SAMDUO"
    assert info["model"] == "Nex E6000"
    assert info["serial_number"] == MOCK_SERIAL
    assert info["sw_version"] == "INV 111 / BMS 106 / EMS 115"
