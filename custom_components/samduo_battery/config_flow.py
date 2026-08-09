"""Config flow for the SAMDUO Battery (Local) integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_CONTROL_TIMEOUT,
    CONF_KEEPALIVE_INTERVAL,
    CONF_MAX_CHARGE_POWER,
    CONF_MAX_DISCHARGE_POWER,
    CONF_MODEL,
    CONF_POLL_INTERVAL,
    CONF_SERIAL,
    DEFAULT_CONTROL_TIMEOUT,
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_MAX_POWER,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DOMAIN,
    MIN_POLL_INTERVAL,
    PN_IGNORED_PREFIXES,
    PN_MODEL_NAMES,
    POWER_LIMIT_MAX,
    POWER_LIMIT_MIN,
    SERVICE_CHECK_BACKUP,
)
from .tcp_client import SamduoTcpClient
from .tcp_manager import TCPClientManager

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
    }
)


def _model_from_pn(pn: str) -> str:
    """Map an mDNS TXT ``pn`` ("<modelprefix>-<mac>") to a model name."""
    prefix = pn.split("-")[0].lower()
    return PN_MODEL_NAMES.get(prefix, "")


async def _probe_device(host: str, port: int) -> str | None:
    """Connect, send one 22023, and return the SN from the response header.

    The device does not validate the SN we send, and it stamps its real SN on
    every response - so a probe with the placeholder SN is enough to both
    validate the connection and learn the serial. The manager singleton is
    removed afterwards so a failed flow leaves no state behind.
    """
    client = SamduoTcpClient(host, port)
    try:
        await client.async_connect()
        result = await client.request(SERVICE_CHECK_BACKUP)
        if result is None:
            return None
        return client.device_serial
    finally:
        await client.async_disconnect()
        TCPClientManager.remove_instance(host, port)


class SamduoBatteryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle zeroconf discovery and manual setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> SamduoBatteryOptionsFlow:
        return SamduoBatteryOptionsFlow(config_entry)

    # ── Zeroconf discovery ─────────────────────────────────────────────────

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        properties = {str(k): str(v) for k, v in discovery_info.properties.items()}
        pn = properties.get("pn", "")
        serial = properties.get("sn", "")

        # The SAMDUO P1 Meter advertises the same service type but speaks a
        # different command set - never offer it as a battery.
        if pn.lower().startswith(PN_IGNORED_PREFIXES) or not serial:
            return self.async_abort(reason="not_supported")

        await self.async_set_unique_id(serial)
        host = str(discovery_info.ip_address)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        model = _model_from_pn(pn)
        self._discovered = {
            CONF_HOST: host,
            CONF_PORT: discovery_info.port or DEFAULT_PORT,
            CONF_SERIAL: serial,
            CONF_MODEL: model,
        }
        self.context["title_placeholders"] = {"name": model or DEFAULT_NAME, "host": host}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        model = self._discovered[CONF_MODEL]
        default_name = f"SAMDUO {model}" if model else DEFAULT_NAME
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME].strip() or default_name,
                data={**self._discovered, CONF_NAME: user_input[CONF_NAME].strip() or default_name},
            )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=vol.Schema({vol.Required(CONF_NAME, default=default_name): str}),
            description_placeholders={
                "model": model or "SAMDUO battery",
                "host": self._discovered[CONF_HOST],
                "serial": self._discovered[CONF_SERIAL],
            },
        )

    # ── Manual setup ───────────────────────────────────────────────────────

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            try:
                serial = await _probe_device(host, port)
            except (TimeoutError, OSError, ConnectionError):
                serial = None
            if serial is None:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured(updates={CONF_HOST: host})
                return self.async_create_entry(
                    title=user_input[CONF_NAME].strip(),
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_NAME: user_input[CONF_NAME].strip(),
                        CONF_SERIAL: serial,
                        CONF_MODEL: "",
                    },
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)


class SamduoBatteryOptionsFlow(OptionsFlow):
    """Allow updating host/port/name and the poll interval without re-adding."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self._entry,
                data={
                    **self._entry.data,
                    CONF_HOST: user_input[CONF_HOST].strip(),
                    CONF_PORT: user_input[CONF_PORT],
                    CONF_NAME: user_input[CONF_NAME].strip(),
                },
            )
            new_options = {
                **self._entry.options,
                CONF_POLL_INTERVAL: user_input[CONF_POLL_INTERVAL],
                CONF_MAX_CHARGE_POWER: user_input[CONF_MAX_CHARGE_POWER],
                CONF_MAX_DISCHARGE_POWER: user_input[CONF_MAX_DISCHARGE_POWER],
                CONF_KEEPALIVE_INTERVAL: user_input[CONF_KEEPALIVE_INTERVAL],
                CONF_CONTROL_TIMEOUT: user_input[CONF_CONTROL_TIMEOUT],
            }
            return self.async_create_entry(title="", data=new_options)

        current = self._entry.data
        options = self._entry.options
        power_field = vol.All(vol.Coerce(int), vol.Range(min=POWER_LIMIT_MIN, max=POWER_LIMIT_MAX))
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=current.get(CONF_HOST, "")): str,
                vol.Required(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): vol.Coerce(int),
                vol.Required(CONF_NAME, default=current.get(CONF_NAME, DEFAULT_NAME)): str,
                vol.Required(
                    CONF_POLL_INTERVAL,
                    default=options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL, max=300)),
                vol.Required(
                    CONF_MAX_CHARGE_POWER,
                    default=options.get(CONF_MAX_CHARGE_POWER, DEFAULT_MAX_POWER),
                ): power_field,
                vol.Required(
                    CONF_MAX_DISCHARGE_POWER,
                    default=options.get(CONF_MAX_DISCHARGE_POWER, DEFAULT_MAX_POWER),
                ): power_field,
                vol.Required(
                    CONF_KEEPALIVE_INTERVAL,
                    default=options.get(CONF_KEEPALIVE_INTERVAL, DEFAULT_KEEPALIVE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
                vol.Required(
                    CONF_CONTROL_TIMEOUT,
                    default=options.get(CONF_CONTROL_TIMEOUT, DEFAULT_CONTROL_TIMEOUT),
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=3600)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
