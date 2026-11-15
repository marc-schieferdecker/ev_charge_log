"""Dashboard input field: date of the session per vehicle (optional)."""

from __future__ import annotations

from datetime import date

from homeassistant.components.date import DateEntity, DateEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import EvChargeLogConfigEntry
from .entity import EvChargeLogEntity, vehicle_device

DAY = DateEntityDescription(key="input_date", translation_key="input_date")

# Former datetime input, replaced by this date input; removed on setup so it
# does not stay behind as an unavailable entity.
_LEGACY_TIME_KEY = "input_time"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvChargeLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the date input for every vehicle."""
    coord = entry.runtime_data
    registry = er.async_get(hass)
    for vehicle_id in coord.vehicles:
        legacy = registry.async_get_entity_id(
            "datetime", DOMAIN, f"{vehicle_id}_{_LEGACY_TIME_KEY}"
        )
        if legacy:
            registry.async_remove(legacy)
        async_add_entities(
            [InputDate(coord, vehicle_id, vehicle_device(coord, vehicle_id), DAY)],
            config_subentry_id=vehicle_id,
        )


class InputDate(EvChargeLogEntity, DateEntity):
    """Day of the session; empty means today when booking."""

    @property
    def native_value(self) -> date | None:
        """Return the entered date or None."""
        return self._coord.inputs[self._subentry_id].day

    async def async_set_value(self, value: date) -> None:
        """Store the entered date."""
        self._coord.async_set_input(self._subentry_id, day=value)
