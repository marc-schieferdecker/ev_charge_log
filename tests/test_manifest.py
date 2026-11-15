"""Manifest checks that hassfest also does, so they fail locally first."""

from __future__ import annotations

import json
from pathlib import Path

MANIFEST = (
    Path(__file__).parent.parent / "custom_components/ev_charge_log/manifest.json"
)


def test_manifest_keys_sorted() -> None:
    """hassfest: domain, name, then the other keys in alphabetical order."""
    keys = list(json.loads(MANIFEST.read_text(encoding="utf-8")))
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_manifest_lists_sorted() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    for key in ("after_dependencies", "dependencies", "requirements"):
        assert manifest.get(key, []) == sorted(manifest.get(key, []))
