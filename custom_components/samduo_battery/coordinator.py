"""Data update coordinator for SAMDUO battery devices: polling and control."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_CONTROL_TIMEOUT,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_MAX_POWER,
    DOMAIN,
    MANUFACTURER,
    MIN_POLL_INTERVAL,
    RELEASE_TIMEOUT_S,
)
from .tcp_client import SamduoTcpClient

_LOGGER = logging.getLogger(__name__)


class SamduoBatteryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the device sequentially: 22600 every cycle, 22046 alongside it.

    The spec advertises multi-service messages but firmware 0.0.0.236 rejects
    them ("Too many services"), so each service is its own round-trip
    (~0.1 s each, recon 2026-08-09). 22023 is not polled: 22600 already
    carries ``inv_backup``.

    Control: one write surface, the signed 22045 setpoint (positive =
    discharge, negative = charge, 0 = hold idle). The device's watchdog
    reverts to self-management ``timeoutS`` after the last write, so the
    coordinator re-sends the active setpoint every keepalive interval; if
    Home Assistant dies, the battery frees itself. Releasing control writes a
    short 0 W hold and stops the keepalive.
    """

    # Delay between a SET and the readback that verifies it. The device
    # answers in ~0.1 s and applies setpoints within one 5 s poll; half a
    # second is comfortably past the echo without slowing the UI.
    _WRITE_VERIFY_DELAY_SECONDS: float = 0.5

    # Re-sends after an unconfirmed write. Writes are idempotent (22045
    # replaces the whole command, 22013 sets an absolute state), so
    # re-sending is safe. No retries once the connection-failure streak says
    # sustained outage: each attempt would burn the escalating reconnect
    # cooldown while newer writes queue behind the write lock.
    _WRITE_RETRY_ATTEMPTS: int = 2
    _WRITE_RETRY_DELAY_SECONDS: float = 1.0
    _WRITE_RETRY_OUTAGE_STREAK: int = 3

    # HEMS-conflict detector: with "HEMS Managed" on, the device stores every
    # setpoint while ignoring it - delivered power is the only signal. Honest
    # tracking is within ~0.5 %; observed refusal diverged 500+ W.
    _TRACK_TOLERANCE_W: int = 150
    _TRACK_TOLERANCE_REL: float = 0.10
    _TRACK_CLAMP_MIN_W: int = 100
    _TRACK_MIN_POLLS: int = 3
    _TRACK_MIN_SECONDS: float = 10.0
    _TRACK_SETTLE_GRACE_SECONDS: float = 10.0
    _TRACK_SOC_FULL: float = 99.0
    _TRACK_SOC_EMPTY: float = 5.0

    def __init__(
        self,
        hass: HomeAssistant,
        client: SamduoTcpClient,
        device_name: str,
        serial: str | None,
        model: str,
        poll_interval: int,
        max_charge_power: int = DEFAULT_MAX_POWER,
        max_discharge_power: int = DEFAULT_MAX_POWER,
        keepalive_interval: int = DEFAULT_KEEPALIVE_INTERVAL,
        control_timeout: int = DEFAULT_CONTROL_TIMEOUT,
        entry_id: str | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device_name}",
            update_interval=timedelta(seconds=max(poll_interval, MIN_POLL_INTERVAL)),
        )
        self.client = client
        self.device_name = device_name
        self.model = model
        self._configured_serial = serial
        self.max_charge_power = max_charge_power
        self.max_discharge_power = max_discharge_power
        self.keepalive_interval = keepalive_interval
        self.control_timeout = control_timeout
        self._last_good_data: dict[str, Any] = {}
        self._consecutive_failures = 0
        # Up to this many consecutive bad polls serve the last good frame
        # before the entities go unavailable: one blip must not flap 15+
        # entities.
        self._failure_tolerance = 5
        self.entry_id = entry_id
        # None = not controlling (device self-manages). An int, including 0,
        # is an actively held setpoint that the keepalive refreshes.
        self._commanded_setpoint: int | None = None
        self._last_keepalive = 0.0
        self._track_streak = 0
        self._track_diverging_since: float | None = None
        self._control_blocked = False
        self._setpoint_changed_at = 0.0
        # Rolling audit trail of control writes, surfaced in diagnostics so
        # reported misbehaviour can be correlated with the exact params sent,
        # the device's echo, and the readback verify result.
        self._write_history: deque[dict[str, Any]] = deque(maxlen=20)
        # Serializes whole write sequences (attempt + retries). asyncio locks
        # wake waiters FIFO, so writes complete in issue order and a re-sent
        # (stale) payload can never land after a newer command - the client's
        # io_lock only orders individual sends, not multi-attempt sequences.
        self._write_lock = asyncio.Lock()

    # ── Read state ─────────────────────────────────────────────────────────

    @property
    def device_serial(self) -> str | None:
        """Configured SN, or the one learned from a response header."""
        return self._configured_serial or self.client.device_serial

    @property
    def hub_identifier(self) -> str:
        return self.device_serial or f"{self.client.host}:{self.client.port}"

    @property
    def commanded_setpoint(self) -> int | None:
        """The held setpoint in W (device sign: + discharge), None = released."""
        return self._commanded_setpoint

    @property
    def write_history(self) -> list[dict[str, Any]]:
        """Recent control writes with echo and verify outcomes (newest last)."""
        return list(self._write_history)

    @property
    def control_blocked(self) -> bool:
        """True while the device confirms setpoints but does not deliver them."""
        return self._control_blocked

    @property
    def device_info(self) -> DeviceInfo:
        versions = []
        for label, key in (("INV", "inverter_version"), ("BMS", "bms_version"), ("EMS", "ems_version")):
            value = (self.data or {}).get(key)
            if value is not None:
                versions.append(f"{label} {value}")
        return DeviceInfo(
            identifiers={(DOMAIN, self.hub_identifier)},
            name=self.device_name,
            manufacturer=MANUFACTURER,
            model=self.model or None,
            serial_number=self.device_serial,
            sw_version=" / ".join(versions) or None,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        await self._async_maybe_keepalive()

        device_data = await self.client.get_device_data()
        if device_data is None:
            return self._handle_failed_poll()

        data = dict(device_data)

        # The watchdog state is secondary: a failed 22046 keeps the previous
        # control_* values rather than failing a poll that has fresh telemetry.
        power_config = await self.client.get_power_config()
        if power_config is not None:
            data.update(power_config)
        else:
            for key in ("control_power", "control_timeout", "control_time_left"):
                if key in self._last_good_data:
                    data[key] = self._last_good_data[key]

        self._consecutive_failures = 0
        self._last_good_data = data
        self._evaluate_control_tracking(data)
        return data

    def _handle_failed_poll(self) -> dict[str, Any]:
        self._consecutive_failures += 1
        if self._last_good_data and self._consecutive_failures <= self._failure_tolerance:
            _LOGGER.debug(
                "Poll failed (%d/%d) - serving last good data",
                self._consecutive_failures,
                self._failure_tolerance,
            )
            return self._last_good_data
        raise UpdateFailed(
            f"Device did not answer {self._consecutive_failures} consecutive polls "
            f"(connection failures: {self.client.consecutive_failures})"
        )

    # ── HEMS-conflict tracking detector ────────────────────────────────────

    def _evaluate_control_tracking(self, data: dict[str, Any]) -> None:
        """Confirm HEMS refusal from delivered power diverging from the
        setpoint; the 22046 readback stores the refused config and sees
        nothing. Suppressed polls hold all state, counting neither way.
        """
        commanded = self._commanded_setpoint
        if commanded is None:
            self._clear_tracking(release=True)
            return
        delivered = data.get("battery_power")
        if delivered is None:
            return
        if time.monotonic() - self._setpoint_changed_at < self._TRACK_SETTLE_GRACE_SECONDS:
            return
        soc = data.get("battery_soc")
        if commanded < 0 and soc is not None and soc >= self._TRACK_SOC_FULL:
            return  # cannot charge a full battery
        if commanded > 0 and soc is not None and soc <= self._TRACK_SOC_EMPTY:
            return  # cannot discharge an empty battery
        if data.get("inverter_state") not in (0, None):
            return  # a faulted inverter is not a HEMS conflict

        tolerance = max(self._TRACK_TOLERANCE_W, abs(commanded) * self._TRACK_TOLERANCE_REL)
        if abs(delivered - commanded) <= tolerance:
            self._clear_tracking()
            return
        if commanded != 0 and delivered * commanded > 0 and abs(delivered) >= self._TRACK_CLAMP_MIN_W:
            # Delivery in the commanded direction: app-limit clamp, not refusal.
            self._clear_tracking()
            return

        now = time.monotonic()
        if self._track_diverging_since is None:
            self._track_diverging_since = now
        self._track_streak += 1
        if (
            not self._control_blocked
            and self._track_streak >= self._TRACK_MIN_POLLS
            and now - self._track_diverging_since >= self._TRACK_MIN_SECONDS
        ):
            self._set_blocked(commanded, delivered)

    def _set_blocked(self, commanded: int, delivered: float) -> None:
        self._control_blocked = True
        _LOGGER.warning(
            "Battery is not following the %d W setpoint (delivering %s W after %d polls) - "
            "is 'HEMS Managed' switched on in the SAMDUO app?",
            commanded,
            delivered,
            self._track_streak,
        )

    def _clear_tracking(self, *, release: bool = False) -> None:
        """Reset the streak; log once on the blocked-to-clear transition."""
        self._track_streak = 0
        self._track_diverging_since = None
        if not self._control_blocked:
            return
        self._control_blocked = False
        if release:
            _LOGGER.info("Control released while setpoints were being ignored - conflict state cleared")
        else:
            _LOGGER.info("Battery is following setpoints again - conflict state cleared")

    # ── Control writes ─────────────────────────────────────────────────────

    async def async_set_power_setpoint(self, watts: float) -> bool:
        """Command the signed setpoint: + = discharge, − = charge, 0 = hold idle.

        Clamped to the per-direction limits from the options - the device
        itself validates nothing (recon: ±6000 W accepted with code 200), so
        this clamp and the builder's range check are the only protection.
        """
        power = int(round(watts))
        if power > self.max_discharge_power:
            _LOGGER.warning(
                "Discharge setpoint %d W exceeds the configured %d W limit - clamping. "
                "Raise 'Max discharge power' in the integration options to allow more.",
                power,
                self.max_discharge_power,
            )
            power = self.max_discharge_power
        elif power < -self.max_charge_power:
            _LOGGER.warning(
                "Charge setpoint %d W exceeds the configured %d W limit - clamping. "
                "Raise 'Max charge power' in the integration options to allow more.",
                -power,
                self.max_charge_power,
            )
            power = -self.max_charge_power

        success = await self._logged_write(
            f"power_setpoint({power}W)",
            lambda: self.client.set_power_control(power, self.control_timeout),
            expected_echo={"power": power, "timeoutS": self.control_timeout},
            verify=lambda: self._verify_power_config(power),
        )
        if success:
            self._note_setpoint_change(power)
            self._commanded_setpoint = power
            self._last_keepalive = time.monotonic()
            self.async_update_listeners()
        return success

    def _note_setpoint_change(self, power: int) -> None:
        """Restart the settle grace on a material change only: the trim loop's
        small adjustments must not keep resetting the divergence streak."""
        previous = self._commanded_setpoint
        tolerance = max(self._TRACK_TOLERANCE_W, abs(power) * self._TRACK_TOLERANCE_REL)
        material = (
            previous is None
            or (power > 0) != (previous > 0)
            or (power < 0) != (previous < 0)
            or abs(power - previous) > tolerance
        )
        if material:
            self._setpoint_changed_at = time.monotonic()
            self._track_streak = 0
            self._track_diverging_since = None

    async def async_set_backup(self, enabled: bool) -> bool:
        """Enable/disable the backup (off-grid) output.

        The 22013 echo returns the PREVIOUS value (fw 0.0.0.236), so no echo
        comparison here - the readback verify and the next 22600 poll are the
        confirmation.
        """
        success = await self._logged_write(
            f"backup({'on' if enabled else 'off'})",
            lambda: self.client.set_backup(enabled),
            verify=lambda: self._verify_backup(enabled),
        )
        if success:
            if self.data is not None:
                self.data["backup_enabled"] = 1 if enabled else 0
            self.async_update_listeners()
        return success

    async def async_release_control(self) -> bool:
        """Hand the battery back to its own logic.

        Writes a short 0 W hold first so a large setpoint stops within
        seconds instead of running out the full watchdog window; when that
        short window expires the device clears the control config entirely
        (recon Q4) and resumes self-management. Then the keepalive stops.
        """
        if self._commanded_setpoint is None:
            return True
        success = await self._logged_write(
            "release_control",
            lambda: self.client.set_power_control(0, RELEASE_TIMEOUT_S),
            expected_echo={"power": 0, "timeoutS": RELEASE_TIMEOUT_S},
        )
        if success:
            self._commanded_setpoint = None
            self._clear_tracking(release=True)
            _LOGGER.info(
                "Control released - battery resumes self-management in ~%d s",
                RELEASE_TIMEOUT_S,
            )
            self.async_update_listeners()
        return success

    async def _async_maybe_keepalive(self) -> None:
        """Refresh the active setpoint before the device watchdog expires.

        Runs at the top of every poll cycle. A failed keepalive is only
        logged: the whole point of the watchdog is that the device frees
        itself when refreshes stop arriving.
        """
        if self._commanded_setpoint is None:
            return
        if time.monotonic() - self._last_keepalive < self.keepalive_interval:
            return
        success = await self._logged_write(
            f"keepalive({self._commanded_setpoint}W)",
            lambda: self.client.set_power_control(self._commanded_setpoint, self.control_timeout),
            expected_echo={"power": self._commanded_setpoint, "timeoutS": self.control_timeout},
        )
        if success:
            self._last_keepalive = time.monotonic()
        else:
            _LOGGER.warning(
                "Keepalive for %d W setpoint not confirmed - the device reverts to "
                "self-management %d s after the last successful refresh",
                self._commanded_setpoint,
                self.control_timeout,
            )

    # ── Write plumbing ─────────────────────────────────────────────────────

    async def _logged_write(
        self,
        operation: str,
        send,
        expected_echo: dict[str, Any] | None = None,
        verify=None,
    ) -> bool:
        """Send a control write with retries and append it to the audit trail.

        The audit entry is appended before the attempt loop and mutated in
        place, so a write still in flight is already visible in diagnostics.
        Success is decided by the device responding at all; an echo mismatch
        (where the service echoes the new values) and the readback verify are
        WARN-only and never change the return value.
        """
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "operation": operation,
            "response_received": False,
            "attempts": 0,
            "echo": None,
            "echo_match": None,
            "verify_result": None,
        }
        self._write_history.append(entry)
        result: dict[str, Any] | None = None
        async with self._write_lock:
            for attempt in range(1 + self._WRITE_RETRY_ATTEMPTS):
                entry["attempts"] = attempt + 1
                result = await send()
                if result is not None:
                    break
                if attempt >= self._WRITE_RETRY_ATTEMPTS:
                    break
                if self.client.consecutive_failures >= self._WRITE_RETRY_OUTAGE_STREAK:
                    _LOGGER.debug(
                        "SET %s unconfirmed with device unreachable (failure streak %d) - not retrying",
                        operation,
                        self.client.consecutive_failures,
                    )
                    break
                _LOGGER.info(
                    "SET %s unconfirmed - re-sending (retry %d of %d)",
                    operation,
                    attempt + 1,
                    self._WRITE_RETRY_ATTEMPTS,
                )
                await asyncio.sleep(self._WRITE_RETRY_DELAY_SECONDS)
        entry["response_received"] = result is not None
        if result is None:
            _LOGGER.warning(
                "SET %s failed - no response from battery after %d attempt(s)",
                operation,
                entry["attempts"],
            )
            return False
        entry["echo"] = dict(result)
        if expected_echo is not None:
            match = all(result.get(k) == v for k, v in expected_echo.items())
            entry["echo_match"] = match
            if not match:
                _LOGGER.warning(
                    "SET %s echo mismatch: sent %s, device echoed %s",
                    operation,
                    expected_echo,
                    result,
                )
        if verify is not None:
            entry["verify_result"] = await verify()
        return True

    async def _verify_power_config(self, expected_power: int) -> dict[str, Any] | None:
        """Re-read 22046 after a setpoint write, recorded in the audit trail.

        Debug-only: the poll-based tracking detector is the alarm. This
        readback races fast-writing automations (a newer setpoint can land
        before the delayed read) and the device stores even refused configs,
        so a mismatch here is diagnostics material, not a warning.
        """
        await asyncio.sleep(self._WRITE_VERIFY_DELAY_SECONDS)
        config = await self.client.get_power_config()
        if config is None:
            return None
        actual = config.get("control_power")
        match = actual == expected_power
        if not match:
            _LOGGER.debug(
                "Write-back verify mismatch: setpoint %d W but device reports %s W",
                expected_power,
                actual,
            )
        return {"expected": expected_power, "actual": actual, "match": match}

    async def _verify_backup(self, enabled: bool) -> dict[str, Any] | None:
        """Re-read 22023 after a backup write (the 22013 echo is pre-write)."""
        await asyncio.sleep(self._WRITE_VERIFY_DELAY_SECONDS)
        state = await self.client.get_backup_state()
        if state is None:
            return None
        expected = 1 if enabled else 0
        actual = state.get("backup_enabled")
        match = actual == expected
        if not match:
            _LOGGER.warning(
                "Write-back verify mismatch: backup set to %s but device reports %s",
                expected,
                actual,
            )
        return {"expected": expected, "actual": actual, "match": match}
