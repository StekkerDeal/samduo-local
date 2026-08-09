"""Tests for the coordinator write machinery: clamp, retry, ordering, keepalive."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from homeassistant.core import HomeAssistant

from custom_components.samduo_battery.coordinator import SamduoBatteryCoordinator

from .conftest import MOCK_SERIAL


def _make_coordinator(
    hass: HomeAssistant,
    mock_client,
    *,
    max_charge: int = 800,
    max_discharge: int = 800,
) -> SamduoBatteryCoordinator:
    return SamduoBatteryCoordinator(
        hass,
        mock_client,
        "Test Battery",
        serial=MOCK_SERIAL,
        model="Nex E6000",
        poll_interval=5,
        max_charge_power=max_charge,
        max_discharge_power=max_discharge,
        keepalive_interval=30,
        control_timeout=90,
    )


# ── Setpoint: clamp and state commit ───────────────────────────────────────────


async def test_setpoint_writes_power_and_timeout(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)

    assert await coordinator.async_set_power_setpoint(300) is True

    mock_client.set_power_control.assert_awaited_with(300, 90)
    assert coordinator.commanded_setpoint == 300
    entry = coordinator.write_history[-1]
    assert entry["response_received"] is True
    assert entry["echo_match"] is True


async def test_discharge_clamped_with_warning(hass: HomeAssistant, mock_client, caplog) -> None:
    """A command above the direction's limit is clamped, not rejected."""
    coordinator = _make_coordinator(hass, mock_client, max_charge=2400, max_discharge=800)

    assert await coordinator.async_set_power_setpoint(900) is True

    mock_client.set_power_control.assert_awaited_with(800, 90)
    assert coordinator.commanded_setpoint == 800
    assert "Max discharge power" in caplog.text


async def test_charge_clamped_to_charge_limit(hass: HomeAssistant, mock_client, caplog) -> None:
    coordinator = _make_coordinator(hass, mock_client, max_charge=2400, max_discharge=800)

    assert await coordinator.async_set_power_setpoint(-2400) is True
    mock_client.set_power_control.assert_awaited_with(-2400, 90)

    assert await coordinator.async_set_power_setpoint(-3000) is True
    mock_client.set_power_control.assert_awaited_with(-2400, 90)
    assert "Max charge power" in caplog.text


async def test_failed_write_keeps_commanded_state(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    assert await coordinator.async_set_power_setpoint(300) is True

    mock_client.set_power_control = AsyncMock(return_value=None)
    assert await coordinator.async_set_power_setpoint(-500) is False

    # Failed write: the entity keeps showing the last successful command.
    assert coordinator.commanded_setpoint == 300


async def test_zero_is_a_held_setpoint(hass: HomeAssistant, mock_client) -> None:
    """0 is a commanded idle-hold (recon Q5), not a release."""
    coordinator = _make_coordinator(hass, mock_client)

    assert await coordinator.async_set_power_setpoint(0) is True

    mock_client.set_power_control.assert_awaited_with(0, 90)
    assert coordinator.commanded_setpoint == 0


async def test_echo_mismatch_warns_but_succeeds(hass: HomeAssistant, mock_client, caplog) -> None:
    mock_client.set_power_control = AsyncMock(return_value={"power": 250, "timeoutS": 90})
    coordinator = _make_coordinator(hass, mock_client)

    assert await coordinator.async_set_power_setpoint(300) is True

    assert "echo mismatch" in caplog.text
    assert coordinator.write_history[-1]["echo_match"] is False


async def test_verify_mismatch_warns(hass: HomeAssistant, mock_client, caplog) -> None:
    # 22046 readback reports a different power than commanded.
    mock_client.get_power_config = AsyncMock(
        return_value={"control_power": 0, "control_timeout": 90, "control_time_left": 88}
    )
    coordinator = _make_coordinator(hass, mock_client)

    assert await coordinator.async_set_power_setpoint(300) is True

    assert "Write-back verify mismatch" in caplog.text
    assert coordinator.write_history[-1]["verify_result"] == {"expected": 300, "actual": 0, "match": False}


# ── Retry behaviour ────────────────────────────────────────────────────────────


async def test_retry_on_unconfirmed_write(hass: HomeAssistant, mock_client) -> None:
    mock_client.set_power_control = AsyncMock(side_effect=[None, {"power": 300, "timeoutS": 90}])
    coordinator = _make_coordinator(hass, mock_client)

    assert await coordinator.async_set_power_setpoint(300) is True

    assert mock_client.set_power_control.await_count == 2
    entry = coordinator.write_history[-1]
    assert entry["attempts"] == 2
    assert entry["response_received"] is True


async def test_gives_up_after_retries(hass: HomeAssistant, mock_client) -> None:
    mock_client.set_power_control = AsyncMock(return_value=None)
    coordinator = _make_coordinator(hass, mock_client)

    assert await coordinator.async_set_power_setpoint(300) is False

    assert mock_client.set_power_control.await_count == 3  # 1 + 2 retries
    # Verify readback never runs for a failed write.
    mock_client.get_power_config.assert_not_awaited()


async def test_no_retry_during_sustained_outage(hass: HomeAssistant, mock_client) -> None:
    mock_client.set_power_control = AsyncMock(return_value=None)
    mock_client.consecutive_failures = 3
    coordinator = _make_coordinator(hass, mock_client)

    assert await coordinator.async_set_power_setpoint(300) is False

    assert mock_client.set_power_control.await_count == 1


async def test_retry_cannot_overwrite_newer_write(hass: HomeAssistant, mock_client) -> None:
    """The write lock is FIFO: a retrying write finishes all its attempts
    before the next write starts, so a re-sent (stale) payload can never land
    after a newer command and silently overwrite it."""
    calls: list[int] = []

    async def flaky_set(power: int, timeout: int):
        calls.append(power)
        await asyncio.sleep(0)
        return None if len(calls) == 1 else {"power": power, "timeoutS": timeout}

    mock_client.set_power_control = AsyncMock(side_effect=flaky_set)
    coordinator = _make_coordinator(hass, mock_client)
    coordinator._WRITE_RETRY_DELAY_SECONDS = 0.05

    old = asyncio.ensure_future(coordinator.async_set_power_setpoint(100))
    await asyncio.sleep(0.01)  # old write failed attempt 1, sleeping before retry
    new = asyncio.ensure_future(coordinator.async_set_power_setpoint(200))

    assert await asyncio.gather(old, new) == [True, True]
    assert calls == [100, 100, 200]
    assert coordinator.commanded_setpoint == 200


async def test_concurrent_writes_serialize(hass: HomeAssistant, mock_client) -> None:
    """Two concurrent service calls must not interleave their sequences.

    An AsyncMock completes without yielding, so a race test would pass whether
    or not writes are serialized - the suspending sender forces a real
    interleaving opportunity.
    """
    in_flight = 0
    max_in_flight = 0

    async def suspending_set(power: int, timeout: int):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return {"power": power, "timeoutS": timeout}

    async def suspending_backup(enabled: bool):
        return await suspending_set(0, 0)

    mock_client.set_power_control = AsyncMock(side_effect=suspending_set)
    mock_client.set_backup = AsyncMock(side_effect=suspending_backup)
    coordinator = _make_coordinator(hass, mock_client)

    await asyncio.gather(
        coordinator.async_set_power_setpoint(100),
        coordinator.async_set_backup(True),
        coordinator.async_set_power_setpoint(200),
    )

    assert max_in_flight == 1


# ── Keepalive ──────────────────────────────────────────────────────────────────


async def test_keepalive_refreshes_active_setpoint(hass: HomeAssistant, mock_client, monkeypatch) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    fake_time = 1000.0
    monkeypatch.setattr("custom_components.samduo_battery.coordinator.time.monotonic", lambda: fake_time)

    assert await coordinator.async_set_power_setpoint(300) is True
    mock_client.set_power_control.reset_mock()

    # Within the interval: no keepalive.
    fake_time += 10
    await coordinator.async_refresh()
    mock_client.set_power_control.assert_not_awaited()

    # Past the interval: the setpoint is re-sent before polling.
    fake_time += 25
    await coordinator.async_refresh()
    mock_client.set_power_control.assert_awaited_with(300, 90)
    assert coordinator.write_history[-1]["operation"] == "keepalive(300W)"


async def test_keepalive_stops_after_release(hass: HomeAssistant, mock_client, monkeypatch) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    fake_time = 1000.0
    monkeypatch.setattr("custom_components.samduo_battery.coordinator.time.monotonic", lambda: fake_time)

    assert await coordinator.async_set_power_setpoint(300) is True
    assert await coordinator.async_release_control() is True
    mock_client.set_power_control.reset_mock()

    fake_time += 100
    await coordinator.async_refresh()
    mock_client.set_power_control.assert_not_awaited()


async def test_keepalive_not_sent_when_never_commanded(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await coordinator.async_refresh()
    mock_client.set_power_control.assert_not_awaited()


async def test_failed_keepalive_warns_and_keeps_state(hass: HomeAssistant, mock_client, monkeypatch, caplog) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    fake_time = 1000.0
    monkeypatch.setattr("custom_components.samduo_battery.coordinator.time.monotonic", lambda: fake_time)

    assert await coordinator.async_set_power_setpoint(300) is True
    mock_client.set_power_control = AsyncMock(return_value=None)

    fake_time += 35
    await coordinator.async_refresh()

    assert "Keepalive" in caplog.text
    # Still commanded: the next cycle tries again; the device watchdog is the net.
    assert coordinator.commanded_setpoint == 300


# ── Release ────────────────────────────────────────────────────────────────────


async def test_release_writes_short_zero_hold(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    assert await coordinator.async_set_power_setpoint(300) is True

    assert await coordinator.async_release_control() is True

    mock_client.set_power_control.assert_awaited_with(0, 5)
    assert coordinator.commanded_setpoint is None


async def test_release_when_not_controlling_is_noop(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    assert await coordinator.async_release_control() is True
    mock_client.set_power_control.assert_not_awaited()


async def test_failed_release_keeps_controlling(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    assert await coordinator.async_set_power_setpoint(300) is True

    mock_client.set_power_control = AsyncMock(return_value=None)
    assert await coordinator.async_release_control() is False

    # Still controlling: the keepalive keeps refreshing 300 W, which is the
    # honest state - the device never confirmed the 0 W hold.
    assert coordinator.commanded_setpoint == 300


# ── Backup ─────────────────────────────────────────────────────────────────────


async def test_backup_write_updates_data_optimistically(hass: HomeAssistant, mock_client) -> None:
    coordinator = _make_coordinator(hass, mock_client)
    await coordinator.async_refresh()
    assert coordinator.data["backup_enabled"] == 0

    assert await coordinator.async_set_backup(True) is True

    mock_client.set_backup.assert_awaited_with(True)
    assert coordinator.data["backup_enabled"] == 1
    # The 22013 echo carries the PREVIOUS value, so no echo comparison happens.
    assert coordinator.write_history[-1]["echo_match"] is None


async def test_backup_verify_reads_22023(hass: HomeAssistant, mock_client) -> None:
    mock_client.get_backup_state = AsyncMock(return_value={"backup_enabled": 1})
    coordinator = _make_coordinator(hass, mock_client)

    assert await coordinator.async_set_backup(True) is True

    mock_client.get_backup_state.assert_awaited()
    assert coordinator.write_history[-1]["verify_result"] == {"expected": 1, "actual": 1, "match": True}
