"""Number entities for SAMDUO battery devices: the power setpoint."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SamduoBatteryCoordinator
from .entity import raise_set_failed, raise_set_rejected

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SamduoBatteryCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities([SamduoPowerSetpoint(coordinator, config_entry)])


class SamduoPowerSetpoint(CoordinatorEntity[SamduoBatteryCoordinator], NumberEntity):
    """The single control surface: signed setpoint straight through to 22045.

    Positive = discharge, negative = charge, 0 = hold idle - the device's own
    convention and EMHASS's, passed through unmodified. Deliberately the only
    power control: a direction select or slider pair would be a second entity
    claiming the same register, needing sync choreography to keep the pair
    honest. One entity, one truth.

    Step 1 because optimizers command arbitrary watt values; asymmetric
    bounds from the per-direction limits so HA itself rejects an
    out-of-range service call before it reaches the coordinator.
    """

    _attr_has_entity_name = True
    _attr_name = "Power Setpoint"
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = NumberDeviceClass.POWER
    _attr_icon = "mdi:gauge"

    def __init__(self, coordinator: SamduoBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_power_setpoint"
        # Positive = discharge, so the max is the discharge limit. Captured at
        # construction; an options change reloads the entry and rebuilds them.
        self._attr_native_min_value = -coordinator.max_charge_power
        self._attr_native_max_value = coordinator.max_discharge_power

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float:
        """Pure projection of coordinator state: released reads as 0."""
        setpoint = self.coordinator.commanded_setpoint
        return 0 if setpoint is None else setpoint

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_set_native_value(self, value: float) -> None:
        try:
            success = await self.coordinator.async_set_power_setpoint(value)
        except ValueError as err:
            raise_set_rejected(self._attr_name, str(err))
        if not success:
            raise_set_failed(self._attr_name)
