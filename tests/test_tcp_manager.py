"""Tests for the TCP connection manager: singleton registry and backoff."""

from __future__ import annotations

import pytest

from custom_components.samduo_battery.tcp_manager import TCPClientManager


@pytest.fixture(autouse=True)
def _clear_registry():
    TCPClientManager._connections.clear()
    yield
    TCPClientManager._connections.clear()


def test_get_instance_is_singleton_per_host_port() -> None:
    a = TCPClientManager.get_instance("h", 3335)
    b = TCPClientManager.get_instance("h", 3335)
    c = TCPClientManager.get_instance("other", 3335)
    assert a is b
    assert a is not c


def test_remove_instance_allows_fresh_state() -> None:
    a = TCPClientManager.get_instance("h", 3335)
    a.note_failure()
    TCPClientManager.remove_instance("h", 3335)
    b = TCPClientManager.get_instance("h", 3335)
    assert b is not a
    assert b.consecutive_failures == 0


def test_cooldown_escalates_and_caps() -> None:
    manager = TCPClientManager("h", 3335, base_cooldown=2.0, max_cooldown=60.0)
    observed = []
    for _ in range(7):
        observed.append(manager.current_cooldown())
        manager.note_failure()
    assert observed == [2, 4, 8, 16, 32, 60, 60]


def test_success_resets_backoff() -> None:
    manager = TCPClientManager("h", 3335, base_cooldown=2.0, max_cooldown=60.0)
    for _ in range(4):
        manager.note_failure()
    assert manager.current_cooldown() == 32
    manager.note_success()
    assert manager.consecutive_failures == 0
    assert manager.current_cooldown() == 2
