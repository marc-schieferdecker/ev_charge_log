"""Dashboard input field: note of the session per vehicle (optional)."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextEntityDescription, TextMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import NOTE_MAX_LENGTH
from .coordinator import EvChargeLogConfigEntry
from .entity import EvChargeLogEntity, vehicle_device

NOTE = TextEntityDescription(
    key="input_note",
    translation_key="input_note",
    mode=TextMode.TEXT,
    native_max=NOTE_MAX_LENGTH,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvChargeLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the note input for every vehicle."""
    coord = entry.runtime_data
    for vehicle_id in coord.vehicles:
        async_add_entities(
            [InputNote(coord, vehicle_id, vehicle_device(coord, vehicle_id), NOTE)],
            config_subentry_id=vehicle_id,
        )


class InputNote(EvChargeLogEntity, TextEntity):
    """Note of the session; empty means no note."""

    @property
    def native_value(self) -> str:
        """Return the entered note; "" when empty (not "unknown")."""
        return self._coord.inputs[self._subentry_id].note or ""

    async def async_set_value(self, value: str) -> None:
        """Store the note without surrounding spaces; blank is no note."""
        self._coord.async_set_input(self._subentry_id, note=value.strip() or None)
