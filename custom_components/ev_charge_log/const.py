"""Constants for EV Charge Log."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ev_charge_log"

SUBENTRY_TYPE_VEHICLE: Final = "vehicle"
SUBENTRY_TYPE_TARIFF: Final = "tariff"

# Config flow fields. The name is stored as the subentry title, not in data.
CONF_NAME: Final = "name"
CONF_BATTERY_CAPACITY_KWH: Final = "battery_capacity_kwh"
CONF_DEFAULT_TARIFF_ID: Final = "default_tariff_id"
CONF_PRICE_AC: Final = "price_ac"
CONF_PRICE_DC: Final = "price_dc"

CHARGE_TYPE_AC: Final = "ac"
CHARGE_TYPE_DC: Final = "dc"
CHARGE_TYPES: Final = (CHARGE_TYPE_AC, CHARGE_TYPE_DC)

# Device model IDs, used to filter device selectors in actions.
MODEL_ID_VEHICLE: Final = "vehicle"
MODEL_ID_TARIFF: Final = "tariff"

SOURCE_MANUAL: Final = "manual"
SOURCE_REMINDER: Final = "reminder"
SOURCES: Final = (SOURCE_MANUAL, SOURCE_REMINDER)
