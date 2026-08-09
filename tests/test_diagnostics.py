"""Tests for the diagnostics download."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo_battery.const import DOMAIN
from custom_components.samduo_battery.diagnostics import async_get_config_entry_diagnostics

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


async def test_diagnostics_sections_and_redaction(hass: HomeAssistant, mock_client) -> None:
    entry = await _setup(hass)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    for section in ("integration", "config", "device", "live_state", "write_history", "last_poll"):
        assert section in diagnostics
    # The serial must never leave the instance.
    assert diagnostics["config"]["data"]["serial"] == "**REDACTED**"
    assert MOCK_SERIAL not in str(diagnostics)
    assert diagnostics["live_state"]["data"]["battery_soc"] == 69.5


async def test_diagnostics_includes_write_history(hass: HomeAssistant, mock_client) -> None:
    entry = await _setup(hass)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_set_power_setpoint(300)

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    history = diagnostics["write_history"]
    assert len(history) == 1
    assert history[0]["operation"] == "power_setpoint(300W)"
    assert history[0]["response_received"] is True
    assert diagnostics["live_state"]["commanded_setpoint"] == 300


async def test_diagnostics_survives_dead_client(hass: HomeAssistant, mock_client) -> None:
    entry = await _setup(hass)
    mock_client.get_device_data.side_effect = OSError("gone")

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert "error" in diagnostics["last_poll"]
    assert "live_state" in diagnostics
