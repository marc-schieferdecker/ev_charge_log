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
# Stage 2: charge detection
CONF_CHARGING_ENTITY_ID: Final = "charging_entity_id"
CONF_SOC_ENTITY_ID: Final = "soc_entity_id"
CONF_LOCATION_ENTITY_ID: Final = "location_entity_id"
CONF_REMINDER_EXCLUDED_ZONES: Final = "reminder_excluded_zones"
CONF_ZONES: Final = "zones"
# Stage 3: price versions
CONF_VERSIONS: Final = "versions"
CONF_VALID_FROM: Final = "valid_from"
# Stage 4: monthly fee
CONF_MONTHLY_FEE: Final = "monthly_fee"
CONF_FEE_VEHICLE_ID: Final = "fee_vehicle_id"
CONF_FEE_START: Final = "fee_start"
CONF_FEE_END: Final = "fee_end"

ZONE_HOME: Final = "zone.home"

# Sidebar panel (stage 4)
PANEL_URL_PATH: Final = "ev-charge-log"

# Push to phones via the Companion app (stage 4)
CONF_NOTIFY_SERVICES: Final = "notify_services"
EVENT_MOBILE_APP_ACTION: Final = "mobile_app_notification_action"
PUSH_ACTION_PREFIX: Final = "EV_CHARGE_LOG_KWH_"

# Fired when a detected charge ends (not in an excluded zone).
EVENT_CHARGING_FINISHED: Final = "ev_charge_log_charging_finished"

CHARGE_TYPE_AC: Final = "ac"
CHARGE_TYPE_DC: Final = "dc"
CHARGE_TYPES: Final = (CHARGE_TYPE_AC, CHARGE_TYPE_DC)

# Device model IDs, used to filter device selectors in actions.
MODEL_ID_VEHICLE: Final = "vehicle"
MODEL_ID_TARIFF: Final = "tariff"

SOURCE_MANUAL: Final = "manual"
SOURCE_REMINDER: Final = "reminder"
SOURCES: Final = (SOURCE_MANUAL, SOURCE_REMINDER)
