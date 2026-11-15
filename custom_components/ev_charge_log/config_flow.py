"""Config flow for EV Charge Log.

Two config entries, so Home Assistant shows them as two groups: "vehicles"
with vehicle subentries and "tariffs" with tariff subentries. The subentry
title is the display name, the subentry ID the stable ID.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_CHARGING_ENTITY_ID,
    CONF_DEFAULT_TARIFF_ID,
    CONF_FEE_END,
    CONF_FEE_START,
    CONF_FEE_VEHICLE_ID,
    CONF_KIND,
    CONF_LOCATION_ENTITY_ID,
    CONF_MONTHLY_FEE,
    CONF_NAME,
    CONF_PRICE_AC,
    CONF_PRICE_DC,
    CONF_REMINDER_EXCLUDED_ZONES,
    CONF_SOC_ENTITY_ID,
    CONF_VALID_FROM,
    CONF_VERSIONS,
    CONF_ZONES,
    DOMAIN,
    KIND_TARIFFS,
    KIND_VEHICLES,
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


def _vehicle_subentries(hass: HomeAssistant) -> list[ConfigSubentry]:
    """Vehicles of the vehicles entry (empty if it does not exist yet)."""
    entry = hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, KIND_VEHICLES)
    return entry.get_subentries_of_type(SUBENTRY_TYPE_VEHICLE) if entry else []


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
        suggested: dict[str, Any] | None = None,
    ) -> SubentryFlowResult:
        """Name checks, type checks, then create or update the subentry.

        `suggested` replaces the default form values (name + stored data).
        """
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

        if user_input is not None:
            suggested = user_input
        elif suggested is None:
            suggested = {CONF_NAME: current.title, **current.data} if current else {}

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

    Stage 3: editing opens a menu. "edit" corrects name, zones and the prices
    valid today; "add_price" adds a price version valid from a date;
    "delete_price" removes a dated version (the base prices always stay).
    """

    def _schema(self) -> vol.Schema:
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME): _NAME_SELECTOR,
            vol.Optional(CONF_PRICE_AC): _PRICE_SELECTOR,
            vol.Optional(CONF_PRICE_DC): _PRICE_SELECTOR,
            vol.Optional(CONF_ZONES): _ZONES_SELECTOR,
            vol.Optional(CONF_MONTHLY_FEE): _PRICE_SELECTOR,
        }
        vehicles = _vehicle_subentries(self.hass)
        if vehicles:
            fields[vol.Optional(CONF_FEE_VEHICLE_ID)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=v.subentry_id, label=v.title)
                        for v in vehicles
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        fields[vol.Optional(CONF_FEE_START)] = selector.DateSelector()
        fields[vol.Optional(CONF_FEE_END)] = selector.DateSelector()
        return vol.Schema(fields)

    def _validate(
        self, user_input: dict[str, Any], own_id: str | None
    ) -> dict[str, str]:
        if user_input.get(CONF_PRICE_AC) is None and (
            user_input.get(CONF_PRICE_DC) is None
        ):
            return {"base": "price_required"}
        if user_input.get(CONF_MONTHLY_FEE) is not None and not (
            user_input.get(CONF_FEE_VEHICLE_ID) and user_input.get(CONF_FEE_START)
        ):
            return {"base": "fee_incomplete"}
        start, end = user_input.get(CONF_FEE_START), user_input.get(CONF_FEE_END)
        if start and end and end < start:
            return {CONF_FEE_END: "fee_end_before_start"}
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
    def _fee_data(user_input: dict[str, Any]) -> dict[str, Any]:
        """Tariff-level fee settings (not versioned)."""
        return {
            CONF_FEE_VEHICLE_ID: user_input.get(CONF_FEE_VEHICLE_ID) or None,
            CONF_FEE_START: user_input.get(CONF_FEE_START) or None,
            CONF_FEE_END: user_input.get(CONF_FEE_END) or None,
        }

    @classmethod
    def _to_data(cls, user_input: dict[str, Any]) -> dict[str, Any]:
        return {
            CONF_PRICE_AC: _price(user_input, CONF_PRICE_AC),
            CONF_PRICE_DC: _price(user_input, CONF_PRICE_DC),
            CONF_MONTHLY_FEE: _price(user_input, CONF_MONTHLY_FEE),
            CONF_ZONES: list(user_input.get(CONF_ZONES) or []),
            CONF_VERSIONS: [],
            **cls._fee_data(user_input),
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add a tariff (base prices only)."""
        return await self._async_handle(
            "user", user_input, self._schema(), self._to_data
        )

    # --- editing (menu) -------------------------------------------------------

    def _versions(self) -> list[dict[str, Any]]:
        """Dated versions of the edited tariff, sorted by date."""
        stored = self._get_reconfigure_subentry().data.get(CONF_VERSIONS) or []
        return sorted(stored, key=lambda v: v[CONF_VALID_FROM])

    def _today_version_index(self) -> int | None:
        """Index of the dated version valid today; None means base prices."""
        today = dt_util.now().date().isoformat()
        index = None
        for i, version in enumerate(self._versions()):
            if version[CONF_VALID_FROM] <= today:
                index = i
        return index

    def _prices_text(self) -> str:
        data = self._get_reconfigure_subentry().data
        lines = [_version_label("—", data)]
        lines += [_version_label(v[CONF_VALID_FROM], v) for v in self._versions()]
        return "\n".join(lines)

    def _fee_until(self, index: int | None) -> float | None:
        """Monthly fee in effect for the base prices or a dated version."""
        fee = self._get_reconfigure_subentry().data.get(CONF_MONTHLY_FEE)
        if index is not None:
            for version in self._versions()[: index + 1]:
                if version.get(CONF_MONTHLY_FEE) is not None:
                    fee = version[CONF_MONTHLY_FEE]
        return fee

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose what to change."""
        options = ["edit", "add_price"]
        if self._versions():
            options.append("delete_price")
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=options,
            description_placeholders={
                "prices": self._prices_text(),
                "currency": self.hass.config.currency,
            },
        )

    async def async_step_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Correct name, zones, fee settings and the prices valid today."""
        current = self._get_reconfigure_subentry()
        index = self._today_version_index()
        versions = self._versions()
        prices = versions[index] if index is not None else current.data

        def to_data(values: dict[str, Any]) -> dict[str, Any]:
            new_prices = {
                CONF_PRICE_AC: _price(values, CONF_PRICE_AC),
                CONF_PRICE_DC: _price(values, CONF_PRICE_DC),
                CONF_MONTHLY_FEE: _price(values, CONF_MONTHLY_FEE),
            }
            data = {
                **current.data,
                CONF_ZONES: list(values.get(CONF_ZONES) or []),
                CONF_VERSIONS: versions,
                **self._fee_data(values),
            }
            if index is None:
                data.update(new_prices)
            else:
                data[CONF_VERSIONS] = [
                    {**v, **new_prices} if i == index else v
                    for i, v in enumerate(versions)
                ]
            return data

        return await self._async_handle(
            "edit",
            user_input,
            self._schema(),
            to_data,
            suggested={
                CONF_NAME: current.title,
                CONF_PRICE_AC: prices.get(CONF_PRICE_AC),
                CONF_PRICE_DC: prices.get(CONF_PRICE_DC),
                CONF_MONTHLY_FEE: self._fee_until(index),
                CONF_ZONES: current.data.get(CONF_ZONES) or [],
                CONF_FEE_VEHICLE_ID: current.data.get(CONF_FEE_VEHICLE_ID),
                CONF_FEE_START: current.data.get(CONF_FEE_START),
                CONF_FEE_END: current.data.get(CONF_FEE_END),
            },
        )

    async def async_step_add_price(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add prices valid from a date on."""
        current = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            valid_from = user_input[CONF_VALID_FROM]
            if _price(user_input, CONF_PRICE_AC) is None and (
                _price(user_input, CONF_PRICE_DC) is None
            ):
                errors["base"] = "price_required"
            elif any(v[CONF_VALID_FROM] == valid_from for v in self._versions()):
                errors[CONF_VALID_FROM] = "date_exists"
            else:
                version = {
                    CONF_VALID_FROM: valid_from,
                    CONF_PRICE_AC: _price(user_input, CONF_PRICE_AC),
                    CONF_PRICE_DC: _price(user_input, CONF_PRICE_DC),
                    CONF_MONTHLY_FEE: _price(user_input, CONF_MONTHLY_FEE),
                }
                versions = sorted(
                    [*self._versions(), version], key=lambda v: v[CONF_VALID_FROM]
                )
                return self.async_update_and_abort(
                    self._get_entry(),
                    current,
                    data={**current.data, CONF_VERSIONS: versions},
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_VALID_FROM): selector.DateSelector(),
                vol.Optional(CONF_PRICE_AC): _PRICE_SELECTOR,
                vol.Optional(CONF_PRICE_DC): _PRICE_SELECTOR,
                vol.Optional(CONF_MONTHLY_FEE): _PRICE_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="add_price",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders={
                "tariff": current.title,
                "currency": self.hass.config.currency,
            },
        )

    async def async_step_delete_price(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Remove a dated price version."""
        current = self._get_reconfigure_subentry()
        versions = self._versions()
        if user_input is not None:
            keep = [
                v for v in versions if v[CONF_VALID_FROM] != user_input[CONF_VALID_FROM]
            ]
            return self.async_update_and_abort(
                self._get_entry(),
                current,
                data={**current.data, CONF_VERSIONS: keep},
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_VALID_FROM): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=v[CONF_VALID_FROM],
                                label=_version_label(v[CONF_VALID_FROM], v),
                            )
                            for v in versions
                        ],
                        mode=selector.SelectSelectorMode.LIST,
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="delete_price",
            data_schema=schema,
            description_placeholders={"tariff": current.title},
        )


def _price(values: dict[str, Any], key: str) -> float | None:
    value = values.get(key)
    return float(value) if value is not None else None


def _format_price(value: float | None) -> str:
    return "-" if value is None else f"{value:g}"


def _version_label(valid_from: str, prices: Mapping[str, Any]) -> str:
    """E.g. "2027-01-01: AC 0.62 / DC 0.72 + 12.99" (+ = monthly fee)."""
    label = (
        f"{valid_from}: AC {_format_price(prices.get(CONF_PRICE_AC))}"
        f" / DC {_format_price(prices.get(CONF_PRICE_DC))}"
    )
    if prices.get(CONF_MONTHLY_FEE) is not None:
        label += f" + {_format_price(prices[CONF_MONTHLY_FEE])}"
    return label
