"""Binary sensors for SAMDUO battery devices: the HEMS conflict."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SamduoBatteryCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SamduoBatteryCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([SamduoControlBlockedSensor(coordinator, config_entry)])


class SamduoControlBlockedSensor(CoordinatorEntity[SamduoBatteryCoordinator], BinarySensorEntity):
    """On while the device confirms setpoints without delivering them.

    Off also covers released control: with nothing commanded there is
    nothing to block. Automations that hand the battery to an optimizer
    should gate on this being off.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "external_control_blocked"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SamduoBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_external_control_blocked"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def is_on(self) -> bool:
        return self.coordinator.control_blocked

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, float | None]:
        return {
            "commanded_setpoint": self.coordinator.commanded_setpoint,
            "delivered_power": (self.coordinator.data or {}).get("battery_power"),
        }
