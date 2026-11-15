"""Translation files must be valid JSON and have the same keys."""

from __future__ import annotations

import json
from pathlib import Path

TRANSLATIONS = (
    Path(__file__).parent.parent / "custom_components/ev_charge_log/translations"
)


def _keys(data: object, prefix: str = "") -> set[str]:
    if not isinstance(data, dict):
        return {prefix}
    keys: set[str] = set()
    for key, value in data.items():
        keys |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return keys


def _load(name: str) -> dict:
    return json.loads((TRANSLATIONS / name).read_text(encoding="utf-8"))


def test_translations_are_valid_json() -> None:
    for path in TRANSLATIONS.glob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_de_has_the_same_keys_as_en() -> None:
    assert _keys(_load("de.json")) == _keys(_load("en.json"))
