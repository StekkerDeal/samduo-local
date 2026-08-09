"""Shared control-entity helpers: the failure taxonomy.

Two distinct failures, two distinct messages (a control that fails silently
leaves HA showing a value the device never took):

- ``raise_set_failed``: the command was sent but the device never confirmed
  it - connectivity territory, worth retrying.
- ``raise_set_rejected``: the value was refused before anything was sent
  (builder validation) - retrying the same value is pointless and the user
  deserves the reason in plain words instead of an "Unknown error" traceback.
"""

from __future__ import annotations

from typing import NoReturn

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN


def raise_set_failed(entity_name: str) -> NoReturn:
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="set_failed",
        translation_placeholders={"entity": entity_name},
    )


def raise_set_rejected(entity_name: str, reason: str) -> NoReturn:
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="set_rejected",
        translation_placeholders={"entity": entity_name, "reason": reason},
    )
