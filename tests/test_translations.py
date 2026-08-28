"""Translation invariants: en mirrors strings.json, all languages share one
key tree, and every translation key used in code has a name entry."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "samduo_battery"
LANGUAGES = ("en", "nl", "de", "fr")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _key_tree(node: object, prefix: str = "") -> set[str]:
    if not isinstance(node, dict):
        return {prefix}
    return {key for name, value in node.items() for key in _key_tree(value, f"{prefix}/{name}")}


def test_en_matches_strings() -> None:
    assert _load(COMPONENT / "translations" / "en.json") == _load(COMPONENT / "strings.json")


@pytest.mark.parametrize("language", LANGUAGES)
def test_key_tree_parity(language: str) -> None:
    strings = _load(COMPONENT / "strings.json")
    translation = _load(COMPONENT / "translations" / f"{language}.json")
    assert _key_tree(translation) == _key_tree(strings)


def test_all_translation_keys_have_names() -> None:
    from custom_components.samduo_battery.sensor import _SENSORS

    # Literals, not class attributes: HA's CachedProperties metaclass turns
    # _attr_translation_key into a property at class level. A typo between
    # code and json still fails the entity-id assertions in the entity tests.
    entity = _load(COMPONENT / "strings.json")["entity"]
    used = {
        "sensor": {row[0] for row in _SENSORS} | {"battery_status", "inverter_status"},
        "binary_sensor": {"external_control_blocked"},
        "number": {"power_setpoint"},
        "switch": {"backup_output"},
        "button": {"release_control"},
    }
    assert set(entity) == set(used)
    for platform, keys in used.items():
        for key in keys:
            assert key in entity[platform], f"missing entity.{platform}.{key} in strings.json"
        assert set(entity[platform]) == keys, f"unused entity.{platform} keys: {set(entity[platform]) - keys}"
