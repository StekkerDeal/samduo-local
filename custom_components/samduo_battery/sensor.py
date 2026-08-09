"""Sensors for SAMDUO battery devices."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SamduoBatteryCoordinator

# Sign convention on every power value: positive = discharging, negative =
# charging. This is the device's own convention and matches EMHASS's
# p_batt_forecast natively. Other battery integrations may use the opposite
# convention - values are passed through unmodified, never flipped.

# (key, name, unit, device_class, state_class, icon, diagnostic)
# icon None = let HA pick. The SOC sensor must not get a static icon: a
# battery-device-class percentage sensor without one gets Home Assistant's
# dynamic level icon (mdi:battery-10 ... mdi:battery), which keeps the SOC
# readable from the icon; a static icon would pin one glyph forever.
_SENSORS: list[tuple[str, str, str | None, SensorDeviceClass | None, SensorStateClass, str | None, bool]] = [
    (
        "battery_soc",
        "Battery SOC",
        PERCENTAGE,
        SensorDeviceClass.BATTERY,
        SensorStateClass.MEASUREMENT,
        None,
        False,
    ),
    (
        "battery_power",
        "Battery Power",
        UnitOfPower.WATT,
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
        "mdi:home-battery",
        False,
    ),
    (
        "backup_power",
        "Backup Power",
        UnitOfPower.WATT,
        SensorDeviceClass.POWER,
        SensorStateClass.MEASUREMENT,
        "mdi:power-plug",
        False,
    ),
    (
        "energy_charged",
        "Energy Charged",
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL_INCREASING,
        "mdi:battery-plus",
        False,
    ),
    (
        "energy_discharged",
        "Energy Discharged",
        UnitOfEnergy.KILO_WATT_HOUR,
        SensorDeviceClass.ENERGY,
        SensorStateClass.TOTAL_INCREASING,
        "mdi:battery-minus",
        False,
    ),
    (
        "grid_voltage",
        "Grid Voltage",
        UnitOfElectricPotential.VOLT,
        SensorDeviceClass.VOLTAGE,
        SensorStateClass.MEASUREMENT,
        None,
        True,
    ),
    (
        "grid_frequency",
        "Grid Frequency",
        UnitOfFrequency.HERTZ,
        SensorDeviceClass.FREQUENCY,
        SensorStateClass.MEASUREMENT,
        None,
        True,
    ),
    (
        "offgrid_voltage",
        "Off-grid Voltage",
        UnitOfElectricPotential.VOLT,
        SensorDeviceClass.VOLTAGE,
        SensorStateClass.MEASUREMENT,
        None,
        True,
    ),
    (
        "battery_voltage",
        "Battery Voltage",
        UnitOfElectricPotential.VOLT,
        SensorDeviceClass.VOLTAGE,
        SensorStateClass.MEASUREMENT,
        None,
        True,
    ),
    (
        "battery_soh",
        "Battery SOH",
        PERCENTAGE,
        None,
        SensorStateClass.MEASUREMENT,
        "mdi:battery-heart-variant",
        True,
    ),
    (
        "temperature_1",
        "Inverter Temperature 1",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
        SensorStateClass.MEASUREMENT,
        None,
        True,
    ),
    (
        "temperature_2",
        "Inverter Temperature 2",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
        SensorStateClass.MEASUREMENT,
        None,
        True,
    ),
    (
        "error_code",
        "Error Code",
        None,
        None,
        SensorStateClass.MEASUREMENT,
        "mdi:alert-circle-outline",
        True,
    ),
    (
        "control_time_left",
        "Control Time Remaining",
        UnitOfTime.SECONDS,
        SensorDeviceClass.DURATION,
        SensorStateClass.MEASUREMENT,
        "mdi:timer-outline",
        True,
    ),
]

# Battery Status derives from Battery Power; flows within the deadband are
# reported as Idle so meter noise does not flap the state.
_STATUS_THRESHOLD_W = 10


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SamduoBatteryCoordinator = hass.data[DOMAIN][config_entry.entry_id]
    entities: list[SensorEntity] = [
        SamduoSensor(coordinator, config_entry, key, name, unit, device_class, state_class, icon, diagnostic)
        for key, name, unit, device_class, state_class, icon, diagnostic in _SENSORS
    ]
    entities.append(SamduoBatteryStatusSensor(coordinator, config_entry))
    entities.append(SamduoInverterStatusSensor(coordinator, config_entry))
    async_add_entities(entities)


class SamduoSensor(CoordinatorEntity[SamduoBatteryCoordinator], SensorEntity):
    """A telemetry value from the coordinator's canonical data dict."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SamduoBatteryCoordinator,
        config_entry: ConfigEntry,
        key: str,
        name: str,
        unit: str | None,
        device_class: SensorDeviceClass | None,
        state_class: SensorStateClass,
        icon: str | None,
        diagnostic: bool,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{config_entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        if icon is not None:
            self._attr_icon = icon
        if diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> float | int | None:
        return (self.coordinator.data or {}).get(self._key)


class SamduoBatteryStatusSensor(CoordinatorEntity[SamduoBatteryCoordinator], SensorEntity):
    """Charging / Discharging / Idle, derived from Battery Power."""

    _attr_has_entity_name = True
    _attr_name = "Battery Status"
    _attr_icon = "mdi:battery-sync"

    def __init__(self, coordinator: SamduoBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_battery_status"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str | None:
        power = (self.coordinator.data or {}).get("battery_power")
        if power is None:
            return None
        if power > _STATUS_THRESHOLD_W:
            return "Discharging"
        if power < -_STATUS_THRESHOLD_W:
            return "Charging"
        return "Idle"


class SamduoInverterStatusSensor(CoordinatorEntity[SamduoBatteryCoordinator], SensorEntity):
    """The inv_state enum. Only 0 (normal) is documented; other values are
    exposed raw so users can report them (and SAMDUO can be asked)."""

    _attr_has_entity_name = True
    _attr_name = "Inverter Status"
    _attr_icon = "mdi:state-machine"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SamduoBatteryCoordinator, config_entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{config_entry.entry_id}_inverter_status"

    @property
    def device_info(self) -> DeviceInfo:
        return self.coordinator.device_info

    @property
    def native_value(self) -> str | None:
        state = (self.coordinator.data or {}).get("inverter_state")
        if state is None:
            return None
        return "Normal" if state == 0 else f"Unknown ({state})"
