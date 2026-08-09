"""Button entities for SAMDUO battery devices: release control."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SamduoBatteryCoordinator
from .entity import raise_set_failed

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SamduoBatteryCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([SamduoReleaseControlButton(coordinator, config_entry)])


class SamduoReleaseControlButton(CoordinatorEntity[SamduoBatteryCoordinator], ButtonEntity):
    """Hand the battery back to its own logic.

    Once a setpoint is commanded, the keepalive holds it indefinitely - this
    is the way out: a short 0 W hold, then the watchdog expires and the
    device resumes self-management. Pressing it while not controlling is a
    no-op that still reports success.
    """

    _attr_has_entity_name = True
    _attr_name = "Release Control"
    _attr_icon = "mdi:lock-open-variant"

    def __init__(self, coordinator: SamduoBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_release_control"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_press(self) -> None:
        if not await self.coordinator.async_release_control():
            raise_set_failed(self._attr_name)
