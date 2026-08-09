"""Diagnostics for SAMDUO battery devices.

Every section is fetched defensively - diagnostics must never raise, a
partial download beats none - and the whole payload is redacted before it
leaves the instance.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import SamduoBatteryCoordinator

# The serial identifies the specific unit and appears in every protocol
# header. The defensive group covers fields that do not exist in the current
# protocol but might appear in future firmware payloads - better to redact a
# key that never occurs than to leak one that suddenly does.
_REDACT_KEYS = {
    "serial",
    "sn",
    "unique_id",
    "identifiers",
    "serial_number",
    # Defensive:
    "password",
    "token",
    "ssid",
    "mac",
    "latitude",
    "longitude",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    coordinator: SamduoBatteryCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    payload: dict[str, Any] = {}

    try:
        integration = await async_get_integration(hass, DOMAIN)
        payload["integration"] = {"version": str(integration.version)}
    except Exception as exc:  # noqa: BLE001 - diagnostics must never raise
        payload["integration"] = {"error": repr(exc)}

    payload["config"] = {
        "data": dict(entry.data),
        "options": dict(entry.options),
    }

    if coordinator is None:
        payload["error"] = "coordinator not loaded"
        return async_redact_data(payload, _REDACT_KEYS)

    try:
        payload["device"] = {
            "model": coordinator.model,
            "device_info": dict(coordinator.device_info),
            "last_update_success": coordinator.last_update_success,
            "consecutive_connection_failures": coordinator.client.consecutive_failures,
        }
    except Exception as exc:  # noqa: BLE001
        payload["device"] = {"error": repr(exc)}

    try:
        payload["live_state"] = {
            "data": dict(coordinator.data or {}),
            "commanded_setpoint": coordinator.commanded_setpoint,
            "max_charge_power": coordinator.max_charge_power,
            "max_discharge_power": coordinator.max_discharge_power,
            "keepalive_interval": coordinator.keepalive_interval,
            "control_timeout": coordinator.control_timeout,
        }
    except Exception as exc:  # noqa: BLE001
        payload["live_state"] = {"error": repr(exc)}

    payload["write_history"] = coordinator.write_history

    try:
        payload["last_poll"] = {
            "device_data": await coordinator.client.get_device_data(),
            "power_config": await coordinator.client.get_power_config(),
            "backup_state": await coordinator.client.get_backup_state(),
        }
    except Exception as exc:  # noqa: BLE001
        payload["last_poll"] = {"error": repr(exc)}

    return async_redact_data(payload, _REDACT_KEYS)
