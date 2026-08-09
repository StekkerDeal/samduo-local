"""Tests for the control entities: setpoint number, backup switch, release button."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo_battery.const import DOMAIN

from .conftest import MOCK_SERIAL, MOCK_USER_INPUT

SETPOINT = "number.test_battery_power_setpoint"
BACKUP = "switch.test_battery_backup_output"
RELEASE = "button.test_battery_release_control"


async def _setup(hass: HomeAssistant, options: dict | None = None) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MOCK_SERIAL,
        data={**MOCK_USER_INPUT, "serial": MOCK_SERIAL, "model": "Nex E6000"},
        options=options or {},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_control_entities_created(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    assert hass.states.get(SETPOINT) is not None
    assert hass.states.get(BACKUP) is not None
    assert hass.states.get(RELEASE) is not None


async def test_setpoint_bounds_follow_limits(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass, options={"max_charge_power": 2400, "max_discharge_power": 800})
    state = hass.states.get(SETPOINT)
    # Positive = discharge, so max is the discharge limit.
    assert state.attributes["min"] == -2400
    assert state.attributes["max"] == 800


async def test_setpoint_defaults_to_zero_released(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    assert hass.states.get(SETPOINT).state == "0"


async def test_set_value_commands_device_and_updates_state(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)

    await hass.services.async_call("number", "set_value", {"entity_id": SETPOINT, "value": 300}, blocking=True)

    mock_client.set_power_control.assert_awaited_with(300, 90)
    assert hass.states.get(SETPOINT).state == "300"


async def test_failed_write_raises_set_failed(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    mock_client.set_power_control = AsyncMock(return_value=None)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("number", "set_value", {"entity_id": SETPOINT, "value": 300}, blocking=True)

    assert err.value.translation_key == "set_failed"
    assert hass.states.get(SETPOINT).state == "0"


async def test_backup_switch_round_trip(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    assert hass.states.get(BACKUP).state == "off"

    await hass.services.async_call("switch", "turn_on", {"entity_id": BACKUP}, blocking=True)
    mock_client.set_backup.assert_awaited_with(True)
    assert hass.states.get(BACKUP).state == "on"

    await hass.services.async_call("switch", "turn_off", {"entity_id": BACKUP}, blocking=True)
    mock_client.set_backup.assert_awaited_with(False)
    assert hass.states.get(BACKUP).state == "off"


async def test_backup_switch_failed_write_raises(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)
    mock_client.set_backup = AsyncMock(return_value=None)

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("switch", "turn_on", {"entity_id": BACKUP}, blocking=True)

    assert err.value.translation_key == "set_failed"
    assert hass.states.get(BACKUP).state == "off"


async def test_release_button_returns_setpoint_to_zero(hass: HomeAssistant, mock_client) -> None:
    await _setup(hass)

    await hass.services.async_call("number", "set_value", {"entity_id": SETPOINT, "value": 300}, blocking=True)
    assert hass.states.get(SETPOINT).state == "300"

    await hass.services.async_call("button", "press", {"entity_id": RELEASE}, blocking=True)

    mock_client.set_power_control.assert_awaited_with(0, 5)
    assert hass.states.get(SETPOINT).state == "0"


async def test_options_flow_carries_control_options(hass: HomeAssistant, mock_client) -> None:
    entry = await _setup(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "host": "192.168.1.95",
            "port": 3335,
            "name": "Test Battery",
            "poll_interval": 5,
            "max_charge_power": 2600,
            "max_discharge_power": 2600,
            "keepalive_interval": 20,
            "control_timeout": 120,
        },
    )

    assert result["type"].value == "create_entry"
    assert entry.options["max_charge_power"] == 2600
    assert entry.options["control_timeout"] == 120
