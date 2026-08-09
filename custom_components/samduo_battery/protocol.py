"""Pure protocol helpers for the SAMDUO Open API (JSON over TCP).

Everything in this module is side-effect free and socket free: envelope
construction, stream framing, response parsing, and telemetry scaling.
Keeping it pure makes the protocol fully unit-testable and leaves the door
open for the UDP and RS485 transports without touching the TCP client.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = "1.0"
STATUS_OK = 200

_HEADER_PREFIX = "triplus/ESD/"
_HEADER_REQUEST_SUFFIX = "/thing/action/execute"

# The device does not validate the SN in the request header (recon
# 2026-08-09: a wrong SN got a normal 200) and always answers with its real
# SN, so a placeholder works until the first response teaches us the truth.
SN_PLACEHOLDER = "0"


@dataclass(frozen=True)
class Request:
    """A built request: the wire bytes plus the msgId to match the reply."""

    msg_id: str
    raw: bytes


@dataclass(frozen=True)
class ServiceResult:
    """One entry of a response's services array."""

    service_id: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None


@dataclass(frozen=True)
class Response:
    """A parsed response envelope."""

    sn: str | None
    msg_id: str | None
    code: int | None
    services: list[ServiceResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.code == STATUS_OK

    def first_result(self) -> dict[str, Any] | None:
        for service in self.services:
            if service.result is not None:
                return service.result
        return None

    def first_error(self) -> dict[str, Any] | None:
        for service in self.services:
            if service.error is not None:
                return service.error
        return None


def build_request(
    sn: str,
    service_id: str,
    params: dict[str, Any] | None = None,
    *,
    ack: int = 1,
    trace: int = 0,
    ts: int | None = None,
) -> Request:
    """Build one request message. Max ONE service per message: firmware
    0.0.0.236 rejects larger arrays with error -32700 "Too many services",
    despite the spec advertising multi-service messages.

    ``ack`` defaults to 1 because the device sends NO response at all with
    ``ack:0`` (live test 2026-08-09) — the spec's "may be omitted where not
    necessary" reads as optional, but over TCP every request needs its reply.
    """
    if ts is None:
        ts = int(time.time() * 1000)
    msg_id = str(ts)
    message = {
        "header": f"{_HEADER_PREFIX}{sn}{_HEADER_REQUEST_SUFFIX}",
        "payload": {
            "ver": PROTOCOL_VERSION,
            "msgId": msg_id,
            "contentType": "json",
            "ts": ts,
            "sys": {"ack": ack, "trace": trace},
            "services": [{"serviceId": service_id, "params": params or {}}],
        },
    }
    return Request(msg_id=msg_id, raw=json.dumps(message, separators=(",", ":")).encode("utf-8"))


def extract_frames(buffer: bytes) -> tuple[list[bytes], bytes]:
    """Split a stream buffer into complete top-level JSON objects.

    The device sends bare concatenated compact JSON with no delimiter or
    length prefix, so frames are found by brace depth, ignoring braces inside
    strings. Returns the complete frames and the unconsumed remainder
    (a partial frame stays buffered until the next chunk arrives). Noise
    before the first ``{`` is discarded.
    """
    frames: list[bytes] = []
    start = -1
    depth = 0
    in_string = False
    escaped = False
    consumed = 0
    for i, byte in enumerate(buffer):
        char = chr(byte)
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = i
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0:
                    frames.append(buffer[start : i + 1])
                    consumed = i + 1
    if depth == 0 and start == -1:
        # Nothing open: everything so far is noise or consumed frames.
        return frames, b""
    if depth == 0:
        return frames, buffer[consumed:].lstrip()
    return frames, buffer[start:]


def strip_keys(obj: Any) -> Any:
    """Recursively strip whitespace from dict keys.

    Firmware 0.0.0.236 really does send ``"timeLeft "`` with a trailing
    space (confirmed live, not just a spec typo).
    """
    if isinstance(obj, dict):
        return {str(k).strip(): strip_keys(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [strip_keys(item) for item in obj]
    return obj


def parse_response(frame: bytes) -> Response | None:
    """Parse one response frame; None if it is not a valid response envelope."""
    try:
        message = json.loads(frame.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(message, dict):
        return None
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return None

    sn: str | None = None
    header = message.get("header")
    if isinstance(header, str) and header.startswith(_HEADER_PREFIX):
        parts = header.split("/")
        if len(parts) >= 3 and parts[2]:
            sn = parts[2]

    services: list[ServiceResult] = []
    raw_services = payload.get("services")
    if isinstance(raw_services, list):
        for entry in raw_services:
            if not isinstance(entry, dict):
                continue
            result = entry.get("result")
            error = entry.get("error")
            services.append(
                ServiceResult(
                    service_id=entry.get("serviceId"),
                    result=strip_keys(result) if isinstance(result, dict) else None,
                    error=strip_keys(error) if isinstance(error, dict) else None,
                )
            )

    msg_id = payload.get("msgId")
    code = payload.get("code")
    return Response(
        sn=sn,
        msg_id=str(msg_id) if msg_id is not None else None,
        code=code if isinstance(code, int) else None,
        services=services,
    )


# ── Telemetry scaling ────────────────────────────────────────────────────────

# raw 22600 field -> (canonical key, divisor). Spec §4.1.1 tables, verified
# against a live reading 2026-08-09 (bms_soc 695 = 69.5 %, inv_gridv 2309 =
# 230.9 V, inv_gridp -799 while charging ~800 W). Sign convention everywhere:
# positive = discharging, negative = charging (device native, EMHASS native).
_DEVICE_DATA_FIELDS: dict[str, tuple[str, float]] = {
    "inv_state": ("inverter_state", 1),
    "inv_ver": ("inverter_version", 1),
    "inv_code": ("inverter_code", 1),
    "inv_err1": ("error_code", 1),
    "inv_gridp": ("battery_power", 1),
    "inv_offgridp": ("backup_power", 1),
    "inv_gridAcwh": ("energy_charged", 1000),  # Wh -> kWh, native counter
    "inv_gridAdwh": ("energy_discharged", 1000),
    "inv_gridf": ("grid_frequency", 10),
    "inv_gridv": ("grid_voltage", 10),
    "inv_offgridv": ("offgrid_voltage", 10),
    "inv_backup": ("backup_enabled", 1),
    "inv_temp1": ("temperature_1", 10),
    "inv_temp2": ("temperature_2", 10),
    "bms_ver": ("bms_version", 1),
    "bms_soc": ("battery_soc", 10),  # per-mille, despite the spec's "0.001" unit
    "bms_soh": ("battery_soh", 10),
    "bms_vol": ("battery_voltage", 10),
    "ems_ver": ("ems_version", 1),
}

_POWER_CONFIG_FIELDS: dict[str, tuple[str, float]] = {
    "power": ("control_power", 1),
    "timeoutS": ("control_timeout", 1),
    "timeLeft": ("control_time_left", 1),
}

_BACKUP_FIELDS: dict[str, tuple[str, float]] = {
    "inv_backup": ("backup_enabled", 1),
}


def _scale(result: dict[str, Any], fields: dict[str, tuple[str, float]]) -> dict[str, Any]:
    """Map raw fields to canonical keys, applying divisors.

    Every field is optional (P2800/BP2800 may omit some) and non-numeric
    values are dropped rather than propagated.
    """
    scaled: dict[str, Any] = {}
    for raw_key, (canonical, divisor) in fields.items():
        value = result.get(raw_key)
        if not isinstance(value, int | float) or isinstance(value, bool):
            continue
        scaled[canonical] = value / divisor if divisor != 1 else value
    return scaled


def scale_device_data(result: dict[str, Any]) -> dict[str, Any]:
    """Scale a 22600 result to canonical keys and HA-native units."""
    return _scale(result, _DEVICE_DATA_FIELDS)


def scale_power_config(result: dict[str, Any]) -> dict[str, Any]:
    """Scale a 22046 result (setpoint watchdog state)."""
    return _scale(result, _POWER_CONFIG_FIELDS)


def scale_backup_state(result: dict[str, Any]) -> dict[str, Any]:
    """Scale a 22023 result (backup enablement)."""
    return _scale(result, _BACKUP_FIELDS)
