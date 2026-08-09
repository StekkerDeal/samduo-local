"""SAMDUO battery TCP protocol client.

Request/response is serialized behind an IO lock: the device answers each
request within ~0.2 s, so a background reader task with a future map would
buy nothing. Responses are
matched on msgId; stale frames (a late reply to a request we already gave up
on) are discarded from the shared stream buffer.

Errors never propagate to callers: every public method returns ``None`` on
failure and routes recovery through the manager's backoff/reconnect. Only the
coordinator decides what a ``None`` means.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import protocol
from .const import (
    READ_TIMEOUT_SUSPECT_THRESHOLD,
    RECONNECT_BASE_COOLDOWN,
    RECONNECT_MAX_COOLDOWN,
    RESPONSE_TIMEOUT,
    SERVICE_CHECK_BACKUP,
    SERVICE_DEVICE_DATA,
    SERVICE_GET_POWER_CONFIG,
)
from .tcp_manager import TCPClientManager

_LOGGER = logging.getLogger(__name__)


class _ReadTimeout(Exception):
    """Raised when the device accepts the request but never replies."""


class SamduoTcpClient:
    def __init__(
        self,
        host: str,
        port: int,
        serial: str | None = None,
        timeout: float = 5.0,
        base_cooldown: float = RECONNECT_BASE_COOLDOWN,
        max_cooldown: float = RECONNECT_MAX_COOLDOWN,
    ) -> None:
        self.host = host
        self.port = port
        self._manager = TCPClientManager.get_instance(host, port, timeout, base_cooldown, max_cooldown)
        # The device ignores the SN in the request header and stamps its real
        # SN on every response header; device_serial is learned from there.
        self._serial = serial or protocol.SN_PLACEHOLDER
        self.device_serial: str | None = serial
        self._io_lock = asyncio.Lock()
        self._buffer = b""
        # Consecutive silent reads (connected but no reply). After
        # READ_TIMEOUT_SUSPECT_THRESHOLD we recycle a possibly half-open socket.
        self._read_timeout_streak = 0

    async def async_connect(self) -> None:
        await self._manager._connect()
        self._manager.note_success()

    async def async_disconnect(self) -> None:
        await self._manager.close()

    @property
    def consecutive_failures(self) -> int:
        """Connection-failure streak from the shared manager (0 = healthy)."""
        return self._manager.consecutive_failures

    # ── Public API ─────────────────────────────────────────────────────────

    async def get_device_data(self) -> dict[str, Any] | None:
        """Poll 22600 and return canonical, scaled telemetry."""
        result = await self.request(SERVICE_DEVICE_DATA)
        return protocol.scale_device_data(result) if result is not None else None

    async def get_power_config(self) -> dict[str, Any] | None:
        """Poll 22046 and return the setpoint watchdog state."""
        result = await self.request(SERVICE_GET_POWER_CONFIG)
        return protocol.scale_power_config(result) if result is not None else None

    async def get_backup_state(self) -> dict[str, Any] | None:
        """Poll 22023 and return the backup enablement state."""
        result = await self.request(SERVICE_CHECK_BACKUP)
        return protocol.scale_backup_state(result) if result is not None else None

    async def request(
        self,
        service_id: str,
        params: dict[str, Any] | None = None,
        *,
        ack: int = 1,
    ) -> dict[str, Any] | None:
        """Send one service request and return its raw result dict, or None.

        One service per message — firmware 0.0.0.236 rejects batches with
        error -32700, so callers poll sequentially.
        """
        req = protocol.build_request(self._serial, service_id, params, ack=ack)
        _LOGGER.debug("TX %s -> %s", service_id, req.raw)
        async with self._io_lock:
            try:
                reader, writer = await self._manager.get_reader_writer()
                writer.write(req.raw)
                await writer.drain()
                response = await self._read_response(reader, req.msg_id)
                self._manager.note_success()
                self._read_timeout_streak = 0
            except _ReadTimeout:
                await self._handle_read_timeout(service_id)
                return None
            except (ConnectionResetError, OSError, asyncio.IncompleteReadError) as exc:
                await self._handle_connection_error(service_id, exc)
                return None

        if response.sn and response.sn != protocol.SN_PLACEHOLDER:
            self.device_serial = response.sn
        if not response.ok:
            _LOGGER.warning(
                "Service %s failed: code=%s error=%s",
                service_id,
                response.code,
                response.first_error(),
            )
            return None
        result = response.first_result()
        if result is None:
            _LOGGER.warning("Service %s returned code 200 without a result", service_id)
        return result

    # ── Failure handling ─────────────────────────────────────────────────────

    async def _handle_connection_error(self, service_id: str, exc: Exception) -> None:
        """Log, back off, and reconnect after a connection-level error.

        The cooldown is read before recording the failure, so the first error
        of an outage waits ``base_cooldown`` and only sustained failures
        escalate. A failing reconnect is swallowed here so it can never
        propagate out of the calling request().
        """
        self._buffer = b""
        cooldown = self._manager.current_cooldown()
        _LOGGER.warning(
            "Service %s connection error: %s - reconnecting after %.0fs cooldown",
            service_id,
            exc,
            cooldown,
        )
        self._manager.note_failure()
        await asyncio.sleep(cooldown)
        try:
            await self._manager.reconnect()
        except (TimeoutError, OSError) as reconnect_exc:
            _LOGGER.debug("Service %s reconnect failed: %s", service_id, reconnect_exc)

    async def _handle_read_timeout(self, service_id: str) -> None:
        """Recycle a likely half-open socket after repeated silent reads.

        A single slow response is tolerated; only after
        ``READ_TIMEOUT_SUSPECT_THRESHOLD`` consecutive timeouts do we close
        the socket so the next request reconnects lazily. This is also the
        symptom when another client (e.g. the SAMDUO app over TCP) holds the
        connection: the device silently starves every later connection.
        """
        self._read_timeout_streak += 1
        if self._read_timeout_streak >= READ_TIMEOUT_SUSPECT_THRESHOLD:
            _LOGGER.warning(
                "Service %s: %d consecutive read timeouts - recycling socket "
                "(is another client, e.g. the SAMDUO app, using the device?)",
                service_id,
                self._read_timeout_streak,
            )
            await self._manager.close()
            self._buffer = b""
            self._read_timeout_streak = 0

    async def _read_response(self, reader: asyncio.StreamReader, msg_id: str) -> protocol.Response:
        """Read frames until the response matching ``msg_id`` arrives.

        The buffer persists across requests: a frame split over segments is
        completed by the next chunk, and a stale response left over from a
        timed-out request is consumed and discarded here instead of
        corrupting the next read.
        """
        try:
            async with asyncio.timeout(RESPONSE_TIMEOUT):
                while True:
                    frames, self._buffer = protocol.extract_frames(self._buffer)
                    for frame in frames:
                        response = protocol.parse_response(frame)
                        if response is None:
                            _LOGGER.warning("Discarding unparseable frame: %.200s", frame)
                            continue
                        if response.msg_id != msg_id:
                            _LOGGER.debug(
                                "Discarding stale response msgId=%s (waiting for %s)",
                                response.msg_id,
                                msg_id,
                            )
                            continue
                        _LOGGER.debug("RX <- %s", frame)
                        return response
                    chunk = await reader.read(4096)
                    if not chunk:
                        raise ConnectionResetError("Battery closed connection")
                    self._buffer += chunk
        except TimeoutError:
            _LOGGER.warning(
                "Timed out waiting for msgId=%s (%d bytes buffered): %.300s",
                msg_id,
                len(self._buffer),
                self._buffer.decode("utf-8", errors="replace") if self._buffer else "(empty)",
            )
            raise _ReadTimeout from None
