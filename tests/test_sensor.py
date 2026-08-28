"""Tests for the sensor platform."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo_battery.const import DOMAIN
from custom_components.samduo_battery.sensor import _SENSORS

from .conftest import MOCK_SERIAL, MOCK_USER_INPUT


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MOCK_SERIAL,
        data={**MOCK_USER_INPUT, "serial": MOCK_SERIAL, "model": "Nex E6000"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensor_states_from_live_sample(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)

    assert hass.states.get("sensor.test_battery_battery_soc").state == "69.5"
    assert hass.states.get("sensor.test_battery_battery_power").state == "-799"
    assert hass.states.get("sensor.test_battery_energy_charged").state == "7.959"
    assert hass.states.get("sensor.test_battery_energy_discharged").state == "5.121"
    assert hass.states.get("sensor.test_battery_grid_voltage").state == "230.9"
    assert hass.states.get("sensor.test_battery_grid_frequency").state == "49.9"
    assert hass.states.get("sensor.test_battery_control_time_remaining").state == "0"


async def test_battery_status_derived_from_power(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    # Live sample: -799 W and the device convention negative = charging.
    assert hass.states.get("sensor.test_battery_battery_status").state == "Charging"


async def test_inverter_status_maps_normal(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    assert hass.states.get("sensor.test_battery_inverter_status").state == "Normal"


async def test_energy_sensors_total_increasing(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    for entity_id in ("sensor.test_battery_energy_charged", "sensor.test_battery_energy_discharged"):
        state = hass.states.get(entity_id)
        assert state.attributes["state_class"] == "total_increasing"
        assert state.attributes["device_class"] == "energy"
        assert state.attributes["unit_of_measurement"] == "kWh"


async def test_soc_sensor_has_no_static_icon(hass: HomeAssistant, mock_client) -> None:
    """A static icon would pin the frontend to one glyph; without one, the
    battery device class provides the dynamic level icon."""
    soc_rows = [s for s in _SENSORS if s[0] == "battery_soc"]
    assert len(soc_rows) == 1
    assert soc_rows[0][4] is None  # icon column

    await _setup(hass)
    assert "icon" not in hass.states.get("sensor.test_battery_battery_soc").attributes


async def test_unique_ids_are_entry_scoped(hass: HomeAssistant, mock_client) -> None:
    entry = await _setup(hass)
    registry = er.async_get(hass)

    soc = registry.async_get("sensor.test_battery_battery_soc")
    assert soc.unique_id == f"{entry.entry_id}_battery_soc"
    status = registry.async_get("sensor.test_battery_battery_status")
    assert status.unique_id == f"{entry.entry_id}_battery_status"


async def test_all_table_sensors_created(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    # 14 table sensors + Battery Status + Inverter Status.
    states = [s for s in hass.states.async_all("sensor") if s.entity_id.startswith("sensor.test_battery")]
    assert len(states) == len(_SENSORS) + 2
