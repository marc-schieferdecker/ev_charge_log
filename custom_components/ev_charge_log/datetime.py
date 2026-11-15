"""Dashboard input field: time of the session per vehicle (optional)."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity, DateTimeEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import EvChargeLogConfigEntry
from .entity import EvChargeLogEntity, vehicle_device

TIME = DateTimeEntityDescription(key="input_time", translation_key="input_time")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvChargeLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the time input for every vehicle."""
    coord = entry.runtime_data
    for vehicle_id in coord.vehicles:
        async_add_entities(
            [InputTime(coord, vehicle_id, vehicle_device(coord, vehicle_id), TIME)],
            config_subentry_id=vehicle_id,
        )


class InputTime(EvChargeLogEntity, DateTimeEntity):
    """End of charging; empty means "now" when booking."""

    @property
    def native_value(self) -> datetime | None:
        """Return the entered time (timezone-aware) or None."""
        return self._coord.inputs[self._subentry_id].timestamp

    async def async_set_value(self, value: datetime) -> None:
        """Store the entered time."""
        self._coord.async_set_input(self._subentry_id, timestamp=value)
