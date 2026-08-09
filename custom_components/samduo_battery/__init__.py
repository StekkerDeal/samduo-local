"""SAMDUO Battery - local TCP integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_MODEL,
    CONF_POLL_INTERVAL,
    CONF_SERIAL,
    CONNECT_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import SamduoBatteryCoordinator
from .tcp_client import SamduoTcpClient
from .tcp_manager import TCPClientManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    host: str = entry.data[CONF_HOST]
    port: int = entry.data[CONF_PORT]
    name: str = entry.data[CONF_NAME]

    client = SamduoTcpClient(host, port, serial=entry.data.get(CONF_SERIAL), timeout=CONNECT_TIMEOUT)
    try:
        await client.async_connect()
    except (TimeoutError, OSError, ConnectionError) as exc:
        raise ConfigEntryNotReady(f"Cannot connect to {host}:{port} - {exc}") from exc

    coordinator = SamduoBatteryCoordinator(
        hass,
        client,
        name,
        serial=entry.data.get(CONF_SERIAL),
        model=entry.data.get(CONF_MODEL, ""),
        poll_interval=entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.info("SAMDUO Battery '%s' set up at %s:%s", name, host, port)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: SamduoBatteryCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.async_disconnect()
        TCPClientManager.remove_instance(entry.data[CONF_HOST], entry.data[CONF_PORT])
    return unloaded
