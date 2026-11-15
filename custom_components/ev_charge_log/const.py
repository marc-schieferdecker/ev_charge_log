"""Constants for EV Charge Log."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ev_charge_log"

SUBENTRY_TYPE_VEHICLE: Final = "vehicle"
SUBENTRY_TYPE_TARIFF: Final = "tariff"

CHARGE_TYPE_AC: Final = "ac"
CHARGE_TYPE_DC: Final = "dc"
CHARGE_TYPES: Final = (CHARGE_TYPE_AC, CHARGE_TYPE_DC)

SOURCE_MANUAL: Final = "manual"
SOURCE_REMINDER: Final = "reminder"
SOURCES: Final = (SOURCE_MANUAL, SOURCE_REMINDER)
