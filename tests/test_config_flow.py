"""Tests for the config flow: manual setup, zeroconf discovery, options."""

from __future__ import annotations

from ipaddress import ip_address

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samduo_battery.const import DOMAIN

from .conftest import MOCK_SERIAL, MOCK_USER_INPUT


def _zeroconf_info(pn: str = "samduonexe6000-3076f5630580", sn: str = MOCK_SERIAL) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("192.168.1.95"),
        ip_addresses=[ip_address("192.168.1.95")],
        hostname="SamduoNexE6000-3076F5630580.local.",
        name=f"{pn}._samduo._tcp.local.",
        port=3335,
        type="_samduo._tcp.local.",
        properties={"pn": pn, "sn": sn, "ver": "0.0.0.236"},
    )


# ── Manual flow ────────────────────────────────────────────────────────────────


async def test_user_flow_creates_entry_with_probed_serial(hass: HomeAssistant, mock_client) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], MOCK_USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test Battery"
    assert result["data"]["host"] == "192.168.1.95"
    assert result["data"]["serial"] == MOCK_SERIAL
    assert result["result"].unique_id == MOCK_SERIAL
    # The probe must not leave the shared socket open behind the entry's back.
    mock_client.async_disconnect.assert_awaited()


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_client) -> None:
    mock_client.async_connect.side_effect = OSError("refused")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], MOCK_USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_silent_device_cannot_connect(hass: HomeAssistant, mock_client) -> None:
    # Connected but never answered - the single-client symptom.
    mock_client.request.return_value = None

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], MOCK_USER_INPUT)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_aborts_on_duplicate_serial(hass: HomeAssistant, mock_client) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=MOCK_SERIAL, data=MOCK_USER_INPUT).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], MOCK_USER_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


# ── Zeroconf flow ──────────────────────────────────────────────────────────────


async def test_zeroconf_flow_discovers_battery(hass: HomeAssistant, mock_client) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf_info()
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "SAMDUO Nex E6000"})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        "host": "192.168.1.95",
        "port": 3335,
        "serial": MOCK_SERIAL,
        "model": "Nex E6000",
        "name": "SAMDUO Nex E6000",
    }
    assert result["result"].unique_id == MOCK_SERIAL


async def test_zeroconf_flow_rejects_p1_meter(hass: HomeAssistant, mock_client) -> None:
    info = _zeroconf_info(pn="samduop1meter-88f155afd720", sn="M001ZX16800271")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_zeroconf_flow_updates_host_of_configured_entry(hass: HomeAssistant, mock_client) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MOCK_SERIAL,
        data={**MOCK_USER_INPUT, "host": "192.168.1.50", "serial": MOCK_SERIAL},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zeroconf_info()
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    # Discovery carries the device's current IP - the entry follows it.
    assert entry.data["host"] == "192.168.1.95"


async def test_zeroconf_flow_unknown_model_still_offered(hass: HomeAssistant, mock_client) -> None:
    # A future battery model with an unknown pn prefix must not be rejected.
    info = _zeroconf_info(pn="samduonexp9999-aabbccddeeff", sn="X01TEST0000001")

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_ZEROCONF}, data=info)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"


# ── Options flow ───────────────────────────────────────────────────────────────


async def test_options_flow_updates_data_and_poll_interval(hass: HomeAssistant, mock_client) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=MOCK_SERIAL,
        data={**MOCK_USER_INPUT, "serial": MOCK_SERIAL, "model": "Nex E6000"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"host": "192.168.1.96", "port": 3335, "name": "Renamed", "poll_interval": 10},
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data["host"] == "192.168.1.96"
    assert entry.data["name"] == "Renamed"
    assert entry.data["serial"] == MOCK_SERIAL  # untouched by the merge
    assert entry.options["poll_interval"] == 10
