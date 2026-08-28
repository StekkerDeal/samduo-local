"""Switch entities for SAMDUO battery devices: backup output."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
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
    async_add_entities([SamduoBackupSwitch(coordinator, config_entry)])


class SamduoBackupSwitch(CoordinatorEntity[SamduoBatteryCoordinator], SwitchEntity):
    """Backup (off-grid/EPS) output, via 22013.

    State comes from ``inv_backup`` in the regular 22600 poll, so after a
    write the next cycle (≤5 s) re-confirms what the coordinator already set
    optimistically. The 22013 echo itself reports the pre-write value and is
    never used for state.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "backup_output"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_icon = "mdi:power-plug"

    def __init__(self, coordinator: SamduoBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_backup"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def is_on(self) -> bool | None:
        value = (self.coordinator.data or {}).get("backup_enabled")
        if value is None:
            return None
        return value == 1

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_turn_on(self, **kwargs) -> None:
        if not await self.coordinator.async_set_backup(True):
            raise_set_failed(str(self.name))

    async def async_turn_off(self, **kwargs) -> None:
        if not await self.coordinator.async_set_backup(False):
            raise_set_failed(str(self.name))
