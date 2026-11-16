"""Translation files must be valid JSON and have the same keys."""

from __future__ import annotations

import json
import re
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


def _error_keys() -> set[str]:
    """Translation keys of all ValidationErrors raised in the integration."""
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in TRANSLATIONS.parent.glob("*.py")
    )
    calls = re.findall(
        r"raise ValidationError\(\s*(?:f?\"[^\"]*\"),\s*(f?)\"([^\"]*)\"", source
    )
    keys = {key for is_f, key in calls if not is_f}
    # Keys built from field names.
    assert {key for is_f, key in calls if is_f} == {"negative_{name}", "{name}_range"}
    keys |= {
        f"negative_{name}"
        for name in re.findall(r"_require_non_negative\(\"(\w+)\"", source)
    }
    keys |= {"soc_start_range", "soc_end_range"}
    return {f"error_{key}" for key in keys}


def test_every_validation_error_is_translated() -> None:
    keys = _error_keys()
    assert "error_no_price" in keys
    for name in ("en.json", "de.json"):
        missing = keys - set(_load(name)["exceptions"])
        assert not missing, (name, missing)
