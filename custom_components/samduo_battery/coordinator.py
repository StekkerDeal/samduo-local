"""Data update coordinator for SAMDUO battery devices."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, MANUFACTURER, MIN_POLL_INTERVAL
from .tcp_client import SamduoTcpClient

_LOGGER = logging.getLogger(__name__)


class SamduoBatteryCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the device sequentially: 22600 every cycle, 22046 alongside it.

    The spec advertises multi-service messages but firmware 0.0.0.236 rejects
    them ("Too many services"), so each service is its own round-trip
    (~0.1 s each, recon 2026-08-09). 22023 is not polled: 22600 already
    carries ``inv_backup``.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: SamduoTcpClient,
        device_name: str,
        serial: str | None,
        model: str,
        poll_interval: int,
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
        self._last_good_data: dict[str, Any] = {}
        self._consecutive_failures = 0
        # Up to this many consecutive bad polls serve the last good frame
        # before the entities go unavailable: one blip must not flap 15+
        # entities.
        self._failure_tolerance = 5

    @property
    def device_serial(self) -> str | None:
        """Configured SN, or the one learned from a response header."""
        return self._configured_serial or self.client.device_serial

    @property
    def hub_identifier(self) -> str:
        return self.device_serial or f"{self.client.host}:{self.client.port}"

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
