"""Tests for the External control blocked binary sensor."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo_battery.const import DOMAIN

from .conftest import MOCK_SERIAL, MOCK_USER_INPUT, with_power

BLOCKED = "binary_sensor.test_battery_external_control_blocked"
SETPOINT = "number.test_battery_power_setpoint"


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MOCK_SERIAL,
        data={**MOCK_USER_INPUT, "serial": MOCK_SERIAL, "model": "Nex E6000"},
        options={"max_charge_power": 2600, "max_discharge_power": 2600},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensor_created_off_and_diagnostic(hass: HomeAssistant, mock_client) -> None:
    entry = await _setup(hass)

    state = hass.states.get(BLOCKED)
    assert state is not None
    assert state.state == "off"
    assert state.attributes["device_class"] == "problem"

    registration = er.async_get(hass).async_get(BLOCKED)
    assert registration.entity_category == er.EntityCategory.DIAGNOSTIC
    assert registration.unique_id == f"{entry.entry_id}_external_control_blocked"


async def test_sensor_follows_conflict_state(hass: HomeAssistant, mock_client) -> None:
    entry = await _setup(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]

    await hass.services.async_call("number", "set_value", {"entity_id": SETPOINT, "value": -250}, blocking=True)
    mock_client.get_device_data.return_value = with_power(400)
    for _ in range(3):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(BLOCKED)
    assert state.state == "on"
    assert state.attributes["commanded_setpoint"] == -250
    assert state.attributes["delivered_power"] == 400

    mock_client.get_device_data.return_value = with_power(-249)
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(BLOCKED).state == "off"
