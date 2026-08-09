"""Tests for integration setup and unload."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo_battery.const import DOMAIN

from .conftest import MOCK_SERIAL, MOCK_USER_INPUT


def _make_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=MOCK_SERIAL,
        data={**MOCK_USER_INPUT, "serial": MOCK_SERIAL, "model": "Nex E6000"},
    )


async def test_setup_and_unload_entry(hass: HomeAssistant, mock_client) -> None:
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id in hass.data[DOMAIN]
    mock_client.async_connect.assert_awaited()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert entry.entry_id not in hass.data[DOMAIN]
    mock_client.async_disconnect.assert_awaited()


async def test_setup_retries_when_connect_fails(hass: HomeAssistant, mock_client) -> None:
    mock_client.async_connect.side_effect = OSError("no route")
    entry = _make_entry()
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
