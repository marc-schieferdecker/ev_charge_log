"""Config flow for EV Charge Log.

Two config entries, so Home Assistant shows them as two groups: "vehicles"
with vehicle subentries and "tariffs" with tariff subentries. The subentry
title is the display name, the subentry ID the stable ID.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import voluptuous as vol  # resolves to probatio on HA >= 2026.9
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_CHARGING_ENTITY_ID,
    CONF_DEFAULT_TARIFF_ID,
    CONF_KIND,
    CONF_LOCATION_ENTITY_ID,
    CONF_NAME,
    CONF_PRICE_AC,
    CONF_PRICE_DC,
    CONF_REMINDER_EXCLUDED_ZONES,
    CONF_SOC_ENTITY_ID,
    CONF_ZONES,
    DOMAIN,
    KIND_TARIFFS,
    KINDS,
    SUBENTRY_TYPE_TARIFF,
    SUBENTRY_TYPE_VEHICLE,
    entry_title,
)

_PRICE_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0, step="any", mode=selector.NumberSelectorMode.BOX
    )
)
_CAPACITY_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0.1,
        step="any",
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="kWh",
    )
)
_NAME_SELECTOR = selector.TextSelector()
_CHARGING_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="binary_sensor")
)
_SOC_SELECTOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
_LOCATION_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=["device_tracker", "person"])
)
_ZONES_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="zone", multiple=True)
)


def _tariff_subentries(hass: HomeAssistant) -> list[ConfigSubentry]:
    """Tariffs of the tariffs entry (empty if it does not exist yet)."""
    entry = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, KIND_TARIFFS)
    return entry.get_subentries_of_type(SUBENTRY_TYPE_TARIFF) if entry else []


class EvChargeLogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the vehicles and the tariffs entry (one of each)."""

    VERSION = 2
    MINOR_VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Vehicles entry: vehicle subentries; tariffs entry: tariff subentries."""
        if config_entry.data.get(CONF_KIND) == KIND_TARIFFS:
            return {SUBENTRY_TYPE_TARIFF: TariffSubentryFlow}
        return {SUBENTRY_TYPE_VEHICLE: VehicleSubentryFlow}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set up the integration; the second entry is created automatically."""
        missing = [
            kind
            for kind in KINDS
            if not self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, kind
            )
        ]
        if not missing:
            return self.async_abort(reason="already_configured")
        if user_input is not None:
            return await self._async_create(missing[0])
        return self.async_show_form(step_id="user")

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Create the missing companion entry (started by async_setup_entry)."""
        return await self._async_create(import_data[CONF_KIND])

    async def _async_create(self, kind: str) -> ConfigFlowResult:
        await self.async_set_unique_id(kind)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=entry_title(kind, self.hass.config.language),
            data={CONF_KIND: kind},
        )


class _NamedSubentryFlow(ConfigSubentryFlow):
    """Shared logic: unique names within one subentry type."""

    _placeholders: dict[str, str]

    def _validate(
        self, user_input: dict[str, Any], own_id: str | None
    ) -> dict[str, str]:
        """Type-specific checks; return form errors (may set placeholders)."""
        return {}

    def _name_taken(self, name: str, own_id: str | None = None) -> bool:
        wanted = name.strip().casefold()
        return any(
            sub.title.strip().casefold() == wanted
            for sub in self._get_entry().get_subentries_of_type(self._subentry_type)
            if sub.subentry_id != own_id
        )

    async def _async_handle(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        schema: vol.Schema,
        to_data: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        self._placeholders = {}
        current: ConfigSubentry | None = (
            self._get_reconfigure_subentry()
            if self.source == SOURCE_RECONFIGURE
            else None
        )

        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            own_id = current.subentry_id if current else None
            if not name:
                errors[CONF_NAME] = "name_required"
            elif self._name_taken(name, own_id):
                errors[CONF_NAME] = "name_exists"
            else:
                errors = self._validate(user_input, own_id)
            if not errors:
                data = to_data(user_input)
                if current is None:
                    return self.async_create_entry(title=name, data=data)
                return self.async_update_and_abort(
                    self._get_entry(), current, title=name, data=data
                )

        suggested: dict[str, Any] = {}
        if current is not None:
            suggested = {CONF_NAME: current.title, **current.data}
        if user_input is not None:
            suggested = user_input

        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
            description_placeholders={
                "currency": self.hass.config.currency,
                **self._placeholders,
            },
        )


class VehicleSubentryFlow(_NamedSubentryFlow):
    """Add or change a vehicle.

    The detection entities (stage 2) are optional; without a charging entity
    there is no detection and no reminder, manual booking always works.
    """

    def _schema(self) -> vol.Schema:
        tariffs = _tariff_subentries(self.hass)
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME): _NAME_SELECTOR,
            vol.Optional(CONF_BATTERY_CAPACITY_KWH): _CAPACITY_SELECTOR,
            vol.Optional(CONF_CHARGING_ENTITY_ID): _CHARGING_SELECTOR,
            vol.Optional(CONF_SOC_ENTITY_ID): _SOC_SELECTOR,
            vol.Optional(CONF_LOCATION_ENTITY_ID): _LOCATION_SELECTOR,
            vol.Optional(CONF_REMINDER_EXCLUDED_ZONES): _ZONES_SELECTOR,
        }
        if tariffs:
            fields[vol.Optional(CONF_DEFAULT_TARIFF_ID)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=t.subentry_id, label=t.title)
                        for t in tariffs
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        return vol.Schema(fields)

    @staticmethod
    def _to_data(user_input: dict[str, Any]) -> dict[str, Any]:
        capacity = user_input.get(CONF_BATTERY_CAPACITY_KWH)
        return {
            CONF_BATTERY_CAPACITY_KWH: float(capacity)
            if capacity is not None
            else None,
            CONF_DEFAULT_TARIFF_ID: user_input.get(CONF_DEFAULT_TARIFF_ID) or None,
            CONF_CHARGING_ENTITY_ID: user_input.get(CONF_CHARGING_ENTITY_ID) or None,
            CONF_SOC_ENTITY_ID: user_input.get(CONF_SOC_ENTITY_ID) or None,
            CONF_LOCATION_ENTITY_ID: user_input.get(CONF_LOCATION_ENTITY_ID) or None,
            CONF_REMINDER_EXCLUDED_ZONES: list(
                user_input.get(CONF_REMINDER_EXCLUDED_ZONES) or []
            ),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a vehicle."""
        return await self._async_handle(
            "user", user_input, self._schema(), self._to_data
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a vehicle."""
        return await self._async_handle(
            "reconfigure", user_input, self._schema(), self._to_data
        )


class TariffSubentryFlow(_NamedSubentryFlow):
    """Add or change a tariff.

    AC and DC price are optional, at least one is required. Zones (stage 2)
    preselect the tariff when a charge ends there; a zone belongs to at most
    one tariff. Changing prices never touches booked sessions.
    """

    _SCHEMA = vol.Schema(
        {
            vol.Required(CONF_NAME): _NAME_SELECTOR,
            vol.Optional(CONF_PRICE_AC): _PRICE_SELECTOR,
            vol.Optional(CONF_PRICE_DC): _PRICE_SELECTOR,
            vol.Optional(CONF_ZONES): _ZONES_SELECTOR,
        }
    )

    def _validate(
        self, user_input: dict[str, Any], own_id: str | None
    ) -> dict[str, str]:
        if user_input.get(CONF_PRICE_AC) is None and (
            user_input.get(CONF_PRICE_DC) is None
        ):
            return {"base": "price_required"}
        wanted = set(user_input.get(CONF_ZONES) or [])
        for other in self._get_entry().get_subentries_of_type(SUBENTRY_TYPE_TARIFF):
            if other.subentry_id == own_id:
                continue
            taken = wanted & set(other.data.get(CONF_ZONES) or [])
            if taken:
                self._placeholders = {
                    "tariff": other.title,
                    "zone": ", ".join(sorted(taken)),
                }
                return {CONF_ZONES: "zone_taken"}
        return {}

    @staticmethod
    def _to_data(user_input: dict[str, Any]) -> dict[str, Any]:
        def price(key: str) -> float | None:
            value = user_input.get(key)
            return float(value) if value is not None else None

        return {
            CONF_PRICE_AC: price(CONF_PRICE_AC),
            CONF_PRICE_DC: price(CONF_PRICE_DC),
            CONF_ZONES: list(user_input.get(CONF_ZONES) or []),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a tariff."""
        return await self._async_handle("user", user_input, self._SCHEMA, self._to_data)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Change a tariff."""
        return await self._async_handle(
            "reconfigure", user_input, self._SCHEMA, self._to_data
        )
