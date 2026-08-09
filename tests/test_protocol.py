"""Tests for the pure protocol module: envelopes, framing, parsing, scaling.

The response fixtures are captures from a Nex E6000 (fw 0.0.0.236, recon
2026-08-09) with the serial number anonymized - otherwise byte-identical,
including the firmware's real ``"timeLeft "`` trailing-space key and the
-32700 batch rejection.
"""

from __future__ import annotations

import json

import pytest

from custom_components.samduo_battery import protocol

# Device captures, serial anonymized.
RESPONSE_22023 = (
    b'{"header":"triplus/ESD/E01PYA0000001/thing/action/execute_response",'
    b'"payload":{"ver":"1.0","msgId":"1786288656800","contentType":"json",'
    b'"ts":1786288657000,"code":200,"services":[{"serviceId":"22023",'
    b'"result":{"inv_backup":0}}]}}'
)
RESPONSE_22600 = (
    b'{"header":"triplus/ESD/E01PYA0000001/thing/action/execute_response",'
    b'"payload":{"ver":"1.0","msgId":"1786288656800","contentType":"json",'
    b'"ts":1786288663284,"code":200,"services":[{"serviceId":"22600",'
    b'"result":{"inv_state":0,"inv_ver":111,"inv_code":1,"inv_err1":0,'
    b'"inv_gridp":-799,"inv_offgridp":0,"inv_gridAcwh":7959,"inv_gridAdwh":5121,'
    b'"inv_gridf":499,"inv_gridv":2309,"inv_offgridv":15,"inv_backup":0,'
    b'"inv_temp1":430,"inv_temp2":439,"bms_ver":106,"bms_soc":695,"bms_soh":999,'
    b'"bms_vol":202,"ems_ver":115}}]}}'
)
RESPONSE_22046 = (
    b'{"header":"triplus/ESD/E01PYA0000001/thing/action/execute_response",'
    b'"payload":{"ver":"1.0","msgId":"1786288744843","contentType":"json",'
    b'"ts":1786288744962,"code":200,"services":[{"serviceId":"22046",'
    b'"result":{"power":0,"timeoutS":0,"timeLeft ":0}}]}}'
)
RESPONSE_TOO_MANY = (
    b'{"header":"triplus/ESD/E01PYA0000001/thing/action/execute_response",'
    b'"payload":{"ver":"1.0","msgId":"1786288656800","contentType":"json",'
    b'"ts":1786288669495,"code":400,"services":[{"serviceId":null,'
    b'"error":{"code":-32700,"message":"Too many services (3)"}}]}}'
)


# ── build_request ──────────────────────────────────────────────────────────────


def test_build_request_envelope() -> None:
    req = protocol.build_request("E01PYA0000001", "22600", ts=1786288656800)
    message = json.loads(req.raw)

    assert message["header"] == "triplus/ESD/E01PYA0000001/thing/action/execute"
    payload = message["payload"]
    assert payload["ver"] == "1.0"
    assert payload["msgId"] == "1786288656800"
    assert req.msg_id == "1786288656800"
    assert payload["contentType"] == "json"
    assert payload["ts"] == 1786288656800
    # ack defaults to 1: with ack:0 the device never replies (live 2026-08-09).
    assert payload["sys"] == {"ack": 1, "trace": 0}
    assert payload["services"] == [{"serviceId": "22600", "params": {}}]


def test_build_request_is_compact_and_single_service() -> None:
    req = protocol.build_request("SN", "22045", {"power": 800, "timeoutS": 60}, ack=1)
    assert b" " not in req.raw
    message = json.loads(req.raw)
    assert len(message["payload"]["services"]) == 1
    assert message["payload"]["services"][0]["params"] == {"power": 800, "timeoutS": 60}
    assert message["payload"]["sys"]["ack"] == 1


def test_build_request_generates_timestamp_msgid() -> None:
    req = protocol.build_request("SN", "22600")
    assert req.msg_id.isdigit()
    assert len(req.msg_id) == 13


# ── extract_frames ─────────────────────────────────────────────────────────────


def test_extract_single_frame() -> None:
    frames, rest = protocol.extract_frames(RESPONSE_22023)
    assert frames == [RESPONSE_22023]
    assert rest == b""


def test_extract_coalesced_frames() -> None:
    frames, rest = protocol.extract_frames(RESPONSE_22023 + RESPONSE_22046)
    assert frames == [RESPONSE_22023, RESPONSE_22046]
    assert rest == b""


def test_extract_split_frame_completes_with_next_chunk() -> None:
    frames, rest = protocol.extract_frames(RESPONSE_22600[:200])
    assert frames == []
    assert rest == RESPONSE_22600[:200]

    frames, rest = protocol.extract_frames(rest + RESPONSE_22600[200:])
    assert frames == [RESPONSE_22600]
    assert rest == b""


def test_extract_complete_plus_partial() -> None:
    buffer = RESPONSE_22023 + RESPONSE_22046[:50]
    frames, rest = protocol.extract_frames(buffer)
    assert frames == [RESPONSE_22023]
    assert rest == RESPONSE_22046[:50]


def test_extract_ignores_braces_inside_strings() -> None:
    tricky = b'{"message":"Too many services {3} \\" }","x":1}'
    frames, rest = protocol.extract_frames(tricky)
    assert frames == [tricky]
    assert rest == b""


def test_extract_discards_leading_noise() -> None:
    frames, rest = protocol.extract_frames(b"\r\n garbage" + RESPONSE_22023)
    assert frames == [RESPONSE_22023]
    assert rest == b""


# ── parse_response ─────────────────────────────────────────────────────────────


def test_parse_ok_response() -> None:
    response = protocol.parse_response(RESPONSE_22023)
    assert response is not None
    assert response.sn == "E01PYA0000001"
    assert response.msg_id == "1786288656800"
    assert response.code == 200
    assert response.ok
    assert response.first_result() == {"inv_backup": 0}
    assert response.first_error() is None


def test_parse_error_response() -> None:
    response = protocol.parse_response(RESPONSE_TOO_MANY)
    assert response is not None
    assert not response.ok
    assert response.code == 400
    assert response.first_result() is None
    assert response.first_error() == {"code": -32700, "message": "Too many services (3)"}
    assert response.services[0].service_id is None


def test_parse_strips_result_keys() -> None:
    response = protocol.parse_response(RESPONSE_22046)
    assert response is not None
    # Firmware sends "timeLeft " with a trailing space; the parser must hide that.
    assert response.first_result() == {"power": 0, "timeoutS": 0, "timeLeft": 0}


def test_parse_rejects_garbage() -> None:
    assert protocol.parse_response(b"not json") is None
    assert protocol.parse_response(b'"just a string"') is None
    assert protocol.parse_response(b'{"header":"x"}') is None


def test_strip_keys_recursive() -> None:
    stripped = protocol.strip_keys({" a ": [{"b ": 1}], "c": {" d": 2}})
    assert stripped == {"a": [{"b": 1}], "c": {"d": 2}}


# ── scaling ────────────────────────────────────────────────────────────────────


def test_scale_device_data_live_sample() -> None:
    result = protocol.parse_response(RESPONSE_22600).first_result()
    scaled = protocol.scale_device_data(result)

    assert scaled["battery_soc"] == 69.5  # 695 per-mille
    assert scaled["battery_soh"] == 99.9
    assert scaled["battery_power"] == -799  # charging, device sign convention
    assert scaled["backup_power"] == 0
    assert scaled["energy_charged"] == 7.959  # Wh -> kWh
    assert scaled["energy_discharged"] == 5.121
    assert scaled["grid_frequency"] == 49.9
    assert scaled["grid_voltage"] == 230.9
    assert scaled["offgrid_voltage"] == 1.5
    assert scaled["battery_voltage"] == 20.2
    assert scaled["temperature_1"] == 43.0
    assert scaled["temperature_2"] == 43.9
    assert scaled["inverter_state"] == 0
    assert scaled["error_code"] == 0
    assert scaled["backup_enabled"] == 0
    assert scaled["inverter_version"] == 111
    assert scaled["bms_version"] == 106
    assert scaled["ems_version"] == 115


def test_scale_tolerates_missing_and_bad_fields() -> None:
    # P2800/BP2800 may omit fields; junk values must be dropped, not scaled.
    scaled = protocol.scale_device_data({"bms_soc": 500, "inv_gridv": "oops", "inv_temp1": None})
    assert scaled == {"battery_soc": 50.0}


def test_scale_power_config_uses_stripped_key() -> None:
    result = protocol.parse_response(RESPONSE_22046).first_result()
    assert protocol.scale_power_config(result) == {
        "control_power": 0,
        "control_timeout": 0,
        "control_time_left": 0,
    }


def test_scale_backup_state() -> None:
    assert protocol.scale_backup_state({"inv_backup": 1}) == {"backup_enabled": 1}


# ── control builders ───────────────────────────────────────────────────────────


def test_build_power_control_params() -> None:
    assert protocol.build_power_control_params(300, 60) == {"power": 300, "timeoutS": 60}
    assert protocol.build_power_control_params(-5000, 3600) == {"power": -5000, "timeoutS": 3600}
    # 0 is a real held setpoint (recon Q5), not "absent".
    assert protocol.build_power_control_params(0, 1) == {"power": 0, "timeoutS": 1}


def test_build_power_control_rejects_out_of_range() -> None:
    # The device accepts anything (recon: ±6000 stored with code 200), so the
    # builder is the only guard.
    with pytest.raises(ValueError, match="power 5001"):
        protocol.build_power_control_params(5001, 60)
    with pytest.raises(ValueError, match="power -6000"):
        protocol.build_power_control_params(-6000, 60)
    with pytest.raises(ValueError, match="timeoutS 0"):
        protocol.build_power_control_params(100, 0)
    with pytest.raises(ValueError, match="timeoutS 3601"):
        protocol.build_power_control_params(100, 3601)


def test_build_backup_params() -> None:
    assert protocol.build_backup_params(True) == {"inv_backup": 1}
    assert protocol.build_backup_params(False) == {"inv_backup": 0}
