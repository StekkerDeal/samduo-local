"""Tests for the HEMS-conflict tracking detector.

The refusal signature: the device confirms and stores every setpoint while
delivered power keeps following its own regulation instead.
"""

from __future__ import annotations

import logging
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.samduo_battery.const import DOMAIN, LEARN_MORE_URL_HEMS
from custom_components.samduo_battery.coordinator import SamduoBatteryCoordinator

from .conftest import MOCK_SERIAL, with_power


def _make_coordinator(hass: HomeAssistant, mock_client) -> SamduoBatteryCoordinator:
    return SamduoBatteryCoordinator(
        hass,
        mock_client,
        "Test Battery",
        serial=MOCK_SERIAL,
        model="Nex E6000",
        poll_interval=5,
        max_charge_power=2600,
        max_discharge_power=2600,
        entry_id="test_entry",
    )


async def _command(coordinator, mock_client, watts: float, delivered: float) -> None:
    assert await coordinator.async_set_power_setpoint(watts)
    mock_client.get_device_data.return_value = with_power(delivered)


async def test_blocked_after_three_diverging_polls(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)

    for _ in range(2):
        await coordinator.async_refresh()
        assert not coordinator.control_blocked
    await coordinator.async_refresh()
    assert coordinator.control_blocked


async def test_min_seconds_gates_confirmation(hass: HomeAssistant, mock_client, monkeypatch) -> None:
    monkeypatch.setattr(SamduoBatteryCoordinator, "_TRACK_MIN_SECONDS", 10.0)
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)

    for _ in range(4):
        await coordinator.async_refresh()
    assert not coordinator.control_blocked

    coordinator._track_diverging_since = time.monotonic() - 11
    await coordinator.async_refresh()
    assert coordinator.control_blocked


async def test_settle_grace_holds_polls_after_write(hass: HomeAssistant, mock_client, monkeypatch) -> None:
    monkeypatch.setattr(SamduoBatteryCoordinator, "_TRACK_SETTLE_GRACE_SECONDS", 10.0)
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)

    for _ in range(3):
        await coordinator.async_refresh()
    assert not coordinator.control_blocked
    assert coordinator._track_streak == 0


async def test_clears_on_tracking_poll(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)
    for _ in range(3):
        await coordinator.async_refresh()
    assert coordinator.control_blocked

    mock_client.get_device_data.return_value = with_power(-248)
    await coordinator.async_refresh()
    assert not coordinator.control_blocked


async def test_clears_on_release(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)
    for _ in range(3):
        await coordinator.async_refresh()
    assert coordinator.control_blocked

    assert await coordinator.async_release_control()
    assert not coordinator.control_blocked


async def test_soc_full_suppresses_charge_divergence(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    assert await coordinator.async_set_power_setpoint(-250)
    mock_client.get_device_data.return_value = with_power(30, battery_soc=99.5)

    for _ in range(5):
        await coordinator.async_refresh()
    assert not coordinator.control_blocked


async def test_soc_empty_suppresses_discharge_divergence(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    assert await coordinator.async_set_power_setpoint(250)
    mock_client.get_device_data.return_value = with_power(0, battery_soc=3.0)

    for _ in range(5):
        await coordinator.async_refresh()
    assert not coordinator.control_blocked


async def test_inverter_fault_suppresses_divergence(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    assert await coordinator.async_set_power_setpoint(-250)
    mock_client.get_device_data.return_value = with_power(400, inverter_state=2)

    for _ in range(5):
        await coordinator.async_refresh()
    assert not coordinator.control_blocked


async def test_same_direction_clamp_is_not_refusal(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, 2600, 800)

    for _ in range(5):
        await coordinator.async_refresh()
    assert not coordinator.control_blocked


async def test_idle_hold_divergence_is_detected(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, 0, 400)

    for _ in range(3):
        await coordinator.async_refresh()
    assert coordinator.control_blocked


async def test_trim_adjustments_keep_the_streak(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)
    for _ in range(2):
        await coordinator.async_refresh()

    # A small same-direction adjustment must not reset the streak.
    await _command(coordinator, mock_client, -260, 400)
    await coordinator.async_refresh()
    assert coordinator.control_blocked


async def test_direction_change_resets_the_streak(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)
    for _ in range(2):
        await coordinator.async_refresh()

    await _command(coordinator, mock_client, 500, -100)
    for _ in range(2):
        await coordinator.async_refresh()
    assert not coordinator.control_blocked


async def test_suppression_holds_a_confirmed_block(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)
    for _ in range(3):
        await coordinator.async_refresh()
    assert coordinator.control_blocked

    mock_client.get_device_data.return_value = with_power(30, battery_soc=99.5)
    await coordinator.async_refresh()
    assert coordinator.control_blocked


async def test_repair_issue_created_and_deleted(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    registry = ir.async_get(hass)
    await _command(coordinator, mock_client, -250, 400)
    for _ in range(3):
        await coordinator.async_refresh()

    issue = registry.async_get_issue(DOMAIN, "hems_blocked_test_entry")
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert not issue.is_fixable
    assert issue.learn_more_url == LEARN_MORE_URL_HEMS
    assert issue.translation_placeholders == {"device_name": "Test Battery"}

    mock_client.get_device_data.return_value = with_power(-248)
    await coordinator.async_refresh()
    assert registry.async_get_issue(DOMAIN, "hems_blocked_test_entry") is None


async def test_repair_issue_deleted_on_release(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    registry = ir.async_get(hass)
    await _command(coordinator, mock_client, -250, 400)
    for _ in range(3):
        await coordinator.async_refresh()
    assert registry.async_get_issue(DOMAIN, "hems_blocked_test_entry") is not None

    assert await coordinator.async_release_control()
    assert registry.async_get_issue(DOMAIN, "hems_blocked_test_entry") is None


async def test_logs_only_on_transitions(hass: HomeAssistant, mock_client, caplog) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await _command(coordinator, mock_client, -250, 400)

    with caplog.at_level(logging.INFO, logger="custom_components.samduo_battery.coordinator"):
        for _ in range(6):
            await coordinator.async_refresh()
        warnings = [r for r in caplog.records if "not following" in r.getMessage()]
        assert len(warnings) == 1

        mock_client.get_device_data.return_value = with_power(-248)
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        infos = [r for r in caplog.records if "following setpoints again" in r.getMessage()]
        assert len(infos) == 1
