"""Constants for EV Charge Log."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "ev_charge_log"

SUBENTRY_TYPE_VEHICLE: Final = "vehicle"
SUBENTRY_TYPE_TARIFF: Final = "tariff"

# Two config entries: one holds the vehicles, one the tariffs (as subentries).
# The kind is stored in entry.data and used as the entry's unique_id.
CONF_KIND: Final = "kind"
KIND_VEHICLES: Final = "vehicles"
KIND_TARIFFS: Final = "tariffs"
KINDS: Final = (KIND_VEHICLES, KIND_TARIFFS)
SUBENTRY_TYPE_BY_KIND: Final = {
    KIND_VEHICLES: SUBENTRY_TYPE_VEHICLE,
    KIND_TARIFFS: SUBENTRY_TYPE_TARIFF,
}
ENTRY_TITLES: Final = {
    "de": {KIND_VEHICLES: "Fahrzeuge", KIND_TARIFFS: "Ladetarife"},
    "en": {KIND_VEHICLES: "Vehicles", KIND_TARIFFS: "Tariffs"},
}


def entry_title(kind: str, language: str) -> str:
    """Title of a config entry in the HA language (English fallback)."""
    titles = ENTRY_TITLES.get(language.split("-")[0], ENTRY_TITLES["en"])
    return titles[kind]


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
