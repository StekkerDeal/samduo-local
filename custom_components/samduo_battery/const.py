"""Constants for the SAMDUO Battery (Local) integration."""

from __future__ import annotations

DOMAIN = "samduo_battery"
MANUFACTURER = "SAMDUO"

DEFAULT_PORT = 3335
DEFAULT_NAME = "SAMDUO Battery"

# Recon 2026-08-09 (fw 0.0.0.236): ~10 req/s on a persistent socket showed no
# throttling, so 5 s is comfortable. Floor guards against pathological options.
DEFAULT_POLL_INTERVAL = 5
MIN_POLL_INTERVAL = 2

# Spec §3.3: device-side ACK timeout is 8 s; mirror it as the client-side
# response timeout so we never give up before the device would.
RESPONSE_TIMEOUT = 8.0

CONNECT_TIMEOUT = 5.0
RECONNECT_BASE_COOLDOWN = 2.0
RECONNECT_MAX_COOLDOWN = 60.0
# Consecutive silent reads (connected but no reply) before the socket is
# recycled as half-open.
READ_TIMEOUT_SUSPECT_THRESHOLD = 3

# The five services in SAMDUO Open API Protocol V1.0 — the complete surface.
SERVICE_DEVICE_DATA = "22600"
SERVICE_ENABLE_BACKUP = "22013"
SERVICE_CHECK_BACKUP = "22023"
SERVICE_SET_POWER = "22045"
SERVICE_GET_POWER_CONFIG = "22046"

ZEROCONF_TYPE = "_samduo._tcp.local."

# mDNS TXT "pn" is "<modelprefix>-<mac>". The P1 Meter advertises the same
# service type as the batteries (seen on the LAN 2026-08-09) but speaks a
# different, undocumented command set — never offer it as a battery.
PN_MODEL_NAMES = {
    "samduonexe6000": "Nex E6000",
    "samduonexe6000h": "Nex E6000H",
    "samduonexp2800pro": "Nex P2800 Pro",
    "samduonexbp2800": "Nex BP2800",
}
PN_IGNORED_PREFIXES = ("samduop1meter",)

CONF_SERIAL = "serial"
CONF_MODEL = "model"
CONF_POLL_INTERVAL = "poll_interval"

# Phase 3 (control) options, scaffolded now so the options schema is stable:
# the 22045 watchdog is refreshed every keepalive interval with timeoutS set
# to control_timeout — 3 missed refreshes before the device reverts.
CONF_KEEPALIVE_INTERVAL = "keepalive_interval"
CONF_CONTROL_TIMEOUT = "control_timeout"
DEFAULT_KEEPALIVE_INTERVAL = 30
DEFAULT_CONTROL_TIMEOUT = 90
