"""SAMDUO battery - local TCP integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_CONTROL_TIMEOUT,
    CONF_KEEPALIVE_INTERVAL,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_DISCHARGE_POWER,
    CONF_MODEL,
    CONF_POLL_INTERVAL,
    CONF_SERIAL,
    CONNECT_TIMEOUT,
    DEFAULT_CONTROL_TIMEOUT,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_MAX_POWER,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import SamduoBatteryCoordinator
from .tcp_client import SamduoTcpClient
from .tcp_manager import TCPClientManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SWITCH, Platform.BUTTON]


def resolve_control_options(options: dict) -> tuple[int, int, int, int]:
    """Effective (max_charge, max_discharge, keepalive, control_timeout).

    Resolved at setup instead of migrating the entry, so a rollback keeps
    working. A control timeout at or below the keepalive interval would let
    the device watchdog expire between refreshes, flapping control - repair
    it to three keepalive intervals rather than failing setup.
    """
    charge = int(options.get(CONF_MAX_CHARGE_POWER, DEFAULT_MAX_POWER))
    discharge = int(options.get(CONF_MAX_DISCHARGE_POWER, DEFAULT_MAX_POWER))
    keepalive = int(options.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL))
    timeout = int(options.get(CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT))
    if timeout <= keepalive:
        _LOGGER.warning(
            "Control timeout (%d s) must exceed the keepalive interval (%d s) - using %d s",
            timeout,
            keepalive,
            keepalive * 3,
        )
        timeout = keepalive * 3
    return charge, discharge, keepalive, timeout


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]
    name: str = entry.data[CONF_NAME]

    client = SamduoTcpClient(host, port, serial=entry.data.get(CONF_SERIAL), timeout=CONNECT_TIMEOUT)
    try:
        await client.async_connect()
    except (TimeoutError, OSError, ConnectionError) as exc:
        raise ConfigEntryNotReady(f"Cannot connect to {host}:{port} - {exc}") from exc

    max_charge, max_discharge, keepalive, control_timeout = resolve_control_options(entry.options)
    coordinator = SamduoBatteryCoordinator(
        hass,
        client,
        name,
        serial=entry.data.get(CONF_SERIAL),
        model=entry.data.get(CONF_MODEL, ""),
        poll_interval=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
        max_charge_power=max_charge,
        max_discharge_power=max_discharge,
        keepalive_interval=keepalive,
        control_timeout=control_timeout,
        entry_id=entry.entry_id,
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.info("SAMDUO battery '%s' set up at %s:%s", name, host, port)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: SamduoBatteryCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        # Options changes reload the entry; a stale conflict issue must not
        # outlive the coordinator state that raised it.
        ir.async_delete_issue(hass, DOMAIN, coordinator.hems_issue_id)
        await coordinator.client.async_disconnect()
        TCPClientManager.remove_instance(entry.data[CONF_HOST], entry.data[CONF_PORT])
    return unloaded
