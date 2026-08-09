"""Tests for SamduoTcpClient: msgId matching, framing, backoff, half-open recycle."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.samduo_battery.tcp_client import SamduoTcpClient
from custom_components.samduo_battery.tcp_manager import TCPClientManager

SN = "E01PYA0000001"
LIVE_22600 = {
    "inv_state": 0,
    "inv_gridp": -799,
    "inv_gridAcwh": 7959,
    "inv_gridAdwh": 5121,
    "inv_gridv": 2309,
    "bms_soc": 695,
}


@pytest.fixture(autouse=True)
def _clear_registry():
    TCPClientManager._connections.clear()
    yield
    TCPClientManager._connections.clear()


@pytest.fixture
def recorded_sleeps(monkeypatch):
    """Replace tcp_client's asyncio.sleep so backoff is recorded, not waited."""
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr("custom_components.samduo_battery.tcp_client.asyncio.sleep", fake_sleep)
    return sleeps


def make_response(msg_id: str, service_id: str, *, result=None, error=None, code=200, sn=SN) -> bytes:
    entry: dict = {"serviceId": service_id}
    if result is not None:
        entry["result"] = result
    if error is not None:
        entry["error"] = error
    message = {
        "header": f"triplus/ESD/{sn}/thing/action/execute_response",
        "payload": {
            "ver": "1.0",
            "msgId": msg_id,
            "contentType": "json",
            "ts": 0,
            "code": code,
            "services": [entry],
        },
    }
    return json.dumps(message, separators=(",", ":")).encode()


class FakeDevice:
    """Answers each written request with a canned per-service response.

    Responses are matched to the request's msgId, like the real device, and
    can be split into chunks to exercise the stream scanner.
    """

    def __init__(self, results: dict[str, dict], *, code: int = 200, chunk_size: int | None = None) -> None:
        self._results = results
        self._code = code
        self._chunk_size = chunk_size
        self._pending: list[bytes] = []

    def write(self, data: bytes) -> None:
        request = json.loads(data)
        msg_id = request["payload"]["msgId"]
        service_id = request["payload"]["services"][0]["serviceId"]
        if self._code != 200:
            response = make_response(msg_id, service_id, error={"code": -32700, "message": "boom"}, code=self._code)
        else:
            response = make_response(msg_id, service_id, result=self._results[service_id])
        if self._chunk_size:
            self._pending.extend(response[i : i + self._chunk_size] for i in range(0, len(response), self._chunk_size))
        else:
            self._pending.append(response)

    async def read(self, _n: int) -> bytes:
        if not self._pending:
            raise TimeoutError
        return self._pending.pop(0)

    def make_rw(self):
        reader = AsyncMock(spec=asyncio.StreamReader)
        reader.read = AsyncMock(side_effect=self.read)
        writer = MagicMock(spec=asyncio.StreamWriter)
        writer.write = MagicMock(side_effect=self.write)
        writer.is_closing.return_value = False
        writer.drain = AsyncMock()
        writer.wait_closed = AsyncMock()
        return reader, writer


def _patch_open(monkeypatch, *, return_value=None, side_effect=None):
    mock = AsyncMock(return_value=return_value) if side_effect is None else AsyncMock(side_effect=side_effect)
    monkeypatch.setattr(asyncio, "open_connection", mock)
    return mock


def _make_rw(read_side=None, read_return=b""):
    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.read = AsyncMock(side_effect=read_side) if read_side is not None else AsyncMock(return_value=read_return)
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.is_closing.return_value = False
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    return reader, writer


# ── Round-trips ────────────────────────────────────────────────────────────────


async def test_get_device_data_returns_scaled_telemetry(monkeypatch) -> None:
    device = FakeDevice({"22600": LIVE_22600})
    _patch_open(monkeypatch, return_value=device.make_rw())
    client = SamduoTcpClient("h", 3335)

    data = await client.get_device_data()

    assert data["battery_soc"] == 69.5
    assert data["battery_power"] == -799
    assert data["energy_charged"] == 7.959
    assert client.device_serial == SN  # learned from the response header


async def test_power_config_strips_firmware_key_quirk(monkeypatch) -> None:
    device = FakeDevice({"22046": {"power": 0, "timeoutS": 0, "timeLeft ": 42}})
    _patch_open(monkeypatch, return_value=device.make_rw())
    client = SamduoTcpClient("h", 3335)

    assert await client.get_power_config() == {
        "control_power": 0,
        "control_timeout": 0,
        "control_time_left": 42,
    }


async def test_error_response_returns_none(monkeypatch) -> None:
    device = FakeDevice({"22600": {}}, code=400)
    _patch_open(monkeypatch, return_value=device.make_rw())
    client = SamduoTcpClient("h", 3335)

    assert await client.get_device_data() is None


async def test_split_response_reassembled(monkeypatch) -> None:
    device = FakeDevice({"22600": LIVE_22600}, chunk_size=40)
    _patch_open(monkeypatch, return_value=device.make_rw())
    client = SamduoTcpClient("h", 3335)

    data = await client.get_device_data()
    assert data["battery_soc"] == 69.5


async def test_stale_buffered_response_discarded(monkeypatch) -> None:
    device = FakeDevice({"22023": {"inv_backup": 1}})
    _patch_open(monkeypatch, return_value=device.make_rw())
    client = SamduoTcpClient("h", 3335)
    # A reply to a request we gave up on earlier is still in the buffer.
    client._buffer = make_response("0000000000000", "22046", result={"power": 800})

    assert await client.get_backup_state() == {"backup_enabled": 1}
    assert client._buffer == b""


# ── Connection-error backoff ───────────────────────────────────────────────────


async def test_single_blip_sleeps_base_then_reconnects(monkeypatch, recorded_sleeps) -> None:
    # Reader always reports a closed connection (empty chunk -> ConnectionResetError).
    reader, writer = _make_rw(read_return=b"")
    _patch_open(monkeypatch, return_value=(reader, writer))
    client = SamduoTcpClient("h", 3335, base_cooldown=2, max_cooldown=60)

    assert await client.get_device_data() is None
    assert recorded_sleeps == [2]
    assert client.consecutive_failures == 1


async def test_sustained_outage_escalates_and_caps(monkeypatch, recorded_sleeps) -> None:
    reader, writer = _make_rw(read_return=b"")
    _patch_open(monkeypatch, return_value=(reader, writer))
    client = SamduoTcpClient("h", 3335, base_cooldown=2, max_cooldown=60)

    for _ in range(7):
        assert await client.get_device_data() is None

    assert recorded_sleeps == [2, 4, 8, 16, 32, 60, 60]


async def test_reconnect_failure_is_swallowed(monkeypatch, recorded_sleeps) -> None:
    reader, writer = _make_rw(read_return=b"")
    # Initial connect succeeds; the reconnect attempt raises.
    _patch_open(monkeypatch, side_effect=[(reader, writer), OSError("refused")])
    client = SamduoTcpClient("h", 3335)

    # Must not raise out of the request path.
    assert await client.get_device_data() is None


async def test_recovery_resets_failures(monkeypatch, recorded_sleeps) -> None:
    device = FakeDevice({"22600": LIVE_22600})
    good_reader, good_writer = device.make_rw()
    bad_reader, bad_writer = _make_rw(read_return=b"")
    _patch_open(
        monkeypatch,
        side_effect=[(bad_reader, bad_writer), (bad_reader, bad_writer), (good_reader, good_writer)],
    )
    client = SamduoTcpClient("h", 3335, base_cooldown=2, max_cooldown=60)

    assert await client.get_device_data() is None
    assert await client.get_device_data() is None
    assert (await client.get_device_data())["battery_soc"] == 69.5

    assert recorded_sleeps == [2, 4]
    assert client.consecutive_failures == 0


# ── Half-open socket guard ─────────────────────────────────────────────────────


async def test_read_timeout_below_threshold_no_recycle(monkeypatch, recorded_sleeps) -> None:
    reader, writer = _make_rw(read_side=[TimeoutError, TimeoutError])
    _patch_open(monkeypatch, return_value=(reader, writer))
    client = SamduoTcpClient("h", 3335)
    client._manager.close = AsyncMock()

    assert await client.get_device_data() is None
    assert await client.get_device_data() is None

    client._manager.close.assert_not_called()


async def test_read_timeout_streak_recycles_socket(monkeypatch, recorded_sleeps) -> None:
    reader, writer = _make_rw(read_side=[TimeoutError, TimeoutError, TimeoutError])
    _patch_open(monkeypatch, return_value=(reader, writer))
    client = SamduoTcpClient("h", 3335)
    client._manager.close = AsyncMock()

    for _ in range(3):
        assert await client.get_device_data() is None

    client._manager.close.assert_called_once()
    assert client._read_timeout_streak == 0


async def test_successful_read_resets_timeout_streak(monkeypatch, recorded_sleeps) -> None:
    device = FakeDevice({"22600": LIVE_22600})

    call_count = 0

    async def flaky_read(n: int) -> bytes:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise TimeoutError
        return await device.read(n)

    reader = AsyncMock(spec=asyncio.StreamReader)
    reader.read = AsyncMock(side_effect=flaky_read)
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.write = MagicMock(side_effect=device.write)
    writer.is_closing.return_value = False
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    _patch_open(monkeypatch, return_value=(reader, writer))
    client = SamduoTcpClient("h", 3335)
    client._manager.close = AsyncMock()

    assert await client.get_device_data() is None
    assert await client.get_device_data() is None
    assert (await client.get_device_data())["battery_soc"] == 69.5

    client._manager.close.assert_not_called()
    assert client._read_timeout_streak == 0
