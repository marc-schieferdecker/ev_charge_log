"""Config flow for EV Charge Log.

One central config entry; vehicles and tariffs are config subentries.
The subentry title is the display name, the subentry ID the stable ID.
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
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERY_CAPACITY_KWH,
    CONF_DEFAULT_TARIFF_ID,
    CONF_NAME,
    CONF_PRICE_AC,
    CONF_PRICE_DC,
    DOMAIN,
    SUBENTRY_TYPE_TARIFF,
    SUBENTRY_TYPE_VEHICLE,
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


class EvChargeLogConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the single EV Charge Log entry."""

    VERSION = 1
    MINOR_VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types: vehicles and tariffs."""
        return {
            SUBENTRY_TYPE_VEHICLE: VehicleSubentryFlow,
            SUBENTRY_TYPE_TARIFF: TariffSubentryFlow,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup. Single instance is enforced via manifest."""
        if user_input is not None:
            return self.async_create_entry(title="EV Charge Log", data={})
        return self.async_show_form(step_id="user")


class _NamedSubentryFlow(ConfigSubentryFlow):
    """Shared logic: unique names within one subentry type."""

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
            description_placeholders={"currency": self.hass.config.currency},
        )


class VehicleSubentryFlow(_NamedSubentryFlow):
    """Add or change a vehicle (stage 1 fields only)."""

    def _schema(self) -> vol.Schema:
        tariffs = self._get_entry().get_subentries_of_type(SUBENTRY_TYPE_TARIFF)
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME): _NAME_SELECTOR,
            vol.Optional(CONF_BATTERY_CAPACITY_KWH): _CAPACITY_SELECTOR,
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
    """Add or change a tariff (stage 1 fields only).

    Changing prices never touches booked sessions: their cost is stored.
    """

    _SCHEMA = vol.Schema(
        {
            vol.Required(CONF_NAME): _NAME_SELECTOR,
            vol.Required(CONF_PRICE_AC): _PRICE_SELECTOR,
            vol.Optional(CONF_PRICE_DC): _PRICE_SELECTOR,
        }
    )

    @staticmethod
    def _to_data(user_input: dict[str, Any]) -> dict[str, Any]:
        price_dc = user_input.get(CONF_PRICE_DC)
        return {
            CONF_PRICE_AC: float(user_input[CONF_PRICE_AC]),
            CONF_PRICE_DC: float(price_dc) if price_dc is not None else None,
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
