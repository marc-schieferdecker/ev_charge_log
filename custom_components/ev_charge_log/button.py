"""Dashboard buttons: book the input as a session, or reset the input."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import EvChargeLogConfigEntry
from .entity import EvChargeLogEntity, vehicle_device
from .models import ValidationError

BOOK = ButtonEntityDescription(key="book", translation_key="book")
RESET = ButtonEntityDescription(key="reset_input", translation_key="reset_input")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EvChargeLogConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the buttons for every vehicle."""
    coord = entry.runtime_data
    for vehicle_id in coord.vehicles:
        device = vehicle_device(coord, vehicle_id)
        async_add_entities(
            [
                BookButton(coord, vehicle_id, device, BOOK),
                ResetButton(coord, vehicle_id, device, RESET),
            ],
            config_subentry_id=vehicle_id,
        )


class BookButton(EvChargeLogEntity, ButtonEntity):
    """Book the current input with timestamp "now"."""

    async def async_press(self) -> None:
        """Book; on invalid input show an error and keep the input."""
        if self._coord.inputs[self._subentry_id].energy_kwh is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="energy_required"
            )
        try:
            await self._coord.async_book_input(self._subentry_id, dt_util.now())
        except ValidationError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key=err.key,
                translation_placeholders=err.placeholders or None,
            ) from err


class ResetButton(EvChargeLogEntity, ButtonEntity):
    """Clear all input fields and preselect the default tariff again."""

    async def async_press(self) -> None:
        """Reset the input."""
        self._coord.async_reset_input(self._subentry_id)
